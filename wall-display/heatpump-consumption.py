#!/usr/bin/env python3
"""
Heat pump consumption calculator for arbitrary date ranges.

The Daikin Antlia heat pump reports daily/weekly/monthly consumption that
resets at cycle boundaries.  The utility company meters on arbitrary dates
(e.g. 13 Feb → 12 Mar).

This script uses HA long-term statistics (hourly aggregates kept by the
recorder) to sum consumption over any user-specified date range.

Usage:
    HA_URL=http://... HA_TOKEN=... python heatpump-consumption.py --discover
    HA_URL=http://... HA_TOKEN=... python heatpump-consumption.py --start 2025-02-13 --end 2025-03-12
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from ha_tools.ha_client import HAClient


def get_client() -> HAClient:
    url = os.environ.get("HA_URL")
    token = os.environ.get("HA_TOKEN")
    if not url or not token:
        print("Set HA_URL and HA_TOKEN environment variables.")
        sys.exit(1)
    return HAClient(url, token)


def discover(ha: HAClient) -> None:
    """List all antlia-related statistic IDs available in HA."""
    all_stats = []
    for stat_type in ("mean", "sum"):
        result = ha.ws_command_sync(
            "recorder/list_statistic_ids",
            statistic_type=stat_type,
        )
        if result:
            for s in result:
                s["_stat_type"] = stat_type
            all_stats.extend(result)

    if not all_stats:
        print("No statistics found (or WS call failed).")
        return

    antlia = [s for s in all_stats if "antlia" in s.get("statistic_id", "").lower()]
    if not antlia:
        print("No antlia statistics found. Showing first 50 sensor stats:")
        for s in all_stats[:50]:
            print(f"  {s['statistic_id']}  [{s.get('unit_of_measurement','?')}]  type={s['_stat_type']}")
        return

    print("Antlia statistic IDs:")
    for s in antlia:
        sid = s["statistic_id"]
        unit = s.get("unit_of_measurement", "?")
        source = s.get("source", "?")
        stype = s["_stat_type"]
        print(f"  {sid}  [{unit}]  (source: {source}, type: {stype})")


def probe(ha: HAClient, entity_id: str, days: int = 3) -> None:
    """Fetch a few days of statistics for the given entity to inspect the data shape."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    print(f"\nProbing {entity_id} for last {days} days …")

    # Request all possible fields including sum/change for cumulative sensors
    kwargs = {
        "statistic_ids": [entity_id],
        "period": "hour",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "types": ["sum", "state", "mean", "change"],
    }
    result = ha.ws_command_sync("recorder/statistics_during_period", **kwargs)
    rows = (result or {}).get(entity_id, [])

    if not rows:
        print("  (no data)")
        return

    print(f"  Got {len(rows)} hourly rows.  First 5:")
    for r in rows[:5]:
        print(f"    {r}")
    print(f"  Last 5:")
    for r in rows[-5:]:
        print(f"    {r}")

    # Try to infer total via change in 'state' (cumulative sensors)
    first_state = rows[0].get("state")
    last_state = rows[-1].get("state")
    if first_state is not None and last_state is not None:
        delta = last_state - first_state
        print(f"\n  state change (last - first): {delta:.2f}")

    # Also sum 'mean' (for gauges that report rate/power)
    means = [r["mean"] for r in rows if r.get("mean") is not None]
    if means:
        print(f"  sum of hourly means: {sum(means):.2f}")


YEARLY_ENTITY = "sensor.antlia_climatecontrol_heating_yearly_electrical_consumption"
WEEKLY_ENTITY = "sensor.antlia_climatecontrol_heating_weekly_electrical_consumption"

# TP-Link P110 smart plug on AV stack (TV + Marantz receiver + Apple TV + WiiM)
AV_ENERGY_TODAY = "sensor.av_console_plug_today_s_consumption"
AV_ENERGY_MONTH = "sensor.av_console_plug_this_month_s_consumption"
AV_POWER = "sensor.av_console_plug_current_consumption"


def compute_consumption_smart(
    ha: HAClient,
    start_dt: datetime,
    end_dt: datetime,
) -> float | None:
    """Compute consumption using the most accurate entity combination.

    Uses the yearly entity (resets only on Jan 1) for best accuracy.
    If the period crosses a Jan 1 boundary, stitches in the weekly entity
    for the week containing Jan 1 — since the weekly reset (Monday) almost
    never coincides with Jan 1, it papers over the yearly reset gap.
    """
    tz = start_dt.tzinfo

    # Find all Jan 1 boundaries within the range
    jan1_dates = []
    year = start_dt.year + 1
    while year <= end_dt.year:
        jan1 = datetime(year, 1, 1, 0, 0, 0, tzinfo=tz)
        if start_dt < jan1 < end_dt:
            jan1_dates.append(jan1)
        year += 1

    if not jan1_dates:
        # No yearly reset in range — yearly entity is perfect
        val = ha.get_energy_consumption(YEARLY_ENTITY, start_dt.isoformat(), end_dt.isoformat())
        if val is not None:
            return val
        # Fallback to weekly if yearly has no data
        return ha.get_energy_consumption(WEEKLY_ENTITY, start_dt.isoformat(), end_dt.isoformat())

    # Period crosses Jan 1 — stitch segments together
    total = 0.0
    segments = []
    cursor = start_dt

    for jan1 in jan1_dates:
        # Week containing Jan 1: Monday before → Sunday after
        jan1_weekday = jan1.weekday()  # 0=Mon
        week_start = jan1 - timedelta(days=jan1_weekday)
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)

        # Clamp to our range
        seg_before_end = max(cursor, week_start)
        seg_after_start = min(end_dt, week_end)

        # Segment before the gap week: yearly entity
        if cursor < seg_before_end:
            segments.append(("yearly", cursor, seg_before_end))
        # The gap week itself: weekly entity
        gap_start = max(cursor, week_start)
        gap_end = min(end_dt, week_end)
        if gap_start < gap_end:
            segments.append(("weekly", gap_start, gap_end))
        cursor = gap_end

    # Remaining segment after last gap week: yearly entity
    if cursor < end_dt:
        segments.append(("yearly", cursor, end_dt))

    for entity_key, seg_start, seg_end in segments:
        entity = YEARLY_ENTITY if entity_key == "yearly" else WEEKLY_ENTITY
        val = ha.get_energy_consumption(entity, seg_start.isoformat(), seg_end.isoformat())
        if val is None:
            print(f"  Warning: no data for {entity_key} segment {seg_start.date()}→{seg_end.date()}")
            return None
        total += val

    return total


def verify(ha: HAClient) -> None:
    """Cross-check our sum(change) calculation against reported values.

    Compares the heat pump's own daily/weekly/monthly/yearly counters
    (which reset at cycle boundaries) against our calculated totals
    for the same periods.  Tries every entity × period combination to
    find the most accurate approach.
    """
    tz = ZoneInfo("Europe/Athens")
    now = datetime.now(tz)

    DAILY_ENTITY = "sensor.antlia_climatecontrol_heating_daily_electrical_consumption"
    WEEKLY_ENTITY = "sensor.antlia_climatecontrol_heating_weekly_electrical_consumption"
    MONTHLY_ENTITY = "sensor.antlia_climatecontrol_heating_monthly_electrical_consumption"
    YEARLY_ENTITY = "sensor.antlia_climatecontrol_heating_yearly_electrical_consumption"

    # The heat pump resets: daily at midnight, weekly on Monday,
    # monthly on the 1st, yearly on Jan 1.
    periods = [
        (
            "Daily",
            DAILY_ENTITY,
            now.replace(hour=0, minute=0, second=0, microsecond=0),
        ),
        (
            "Weekly",
            WEEKLY_ENTITY,
            (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0),
        ),
        (
            "Monthly",
            MONTHLY_ENTITY,
            now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        ),
        (
            "Yearly",
            YEARLY_ENTITY,
            now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0),
        ),
    ]

    all_entities = [DAILY_ENTITY, WEEKLY_ENTITY, MONTHLY_ENTITY, YEARLY_ENTITY]
    entity_short = {
        DAILY_ENTITY: "daily",
        WEEKLY_ENTITY: "weekly",
        MONTHLY_ENTITY: "monthly",
        YEARLY_ENTITY: "yearly",
    }

    print(f"Verifying at {now.strftime('%Y-%m-%d %H:%M %Z')}")
    print()

    for label, report_entity, period_start in periods:
        state = ha.get_state(report_entity)
        reported = float(state["state"]) if state else None
        start_iso = period_start.isoformat()
        end_iso = now.isoformat()

        print(f"── {label} (reported: {reported:.0f} kWh, since {period_start.strftime('%Y-%m-%d %H:%M')}) ──")
        print(f"  {'entity':>10} {'hour':>8} {'day':>8}")

        for entity in all_entities:
            ename = entity_short[entity]
            val_h = ha.get_energy_consumption(entity, start_iso, end_iso, period="hour")
            val_d = ha.get_energy_consumption(entity, start_iso, end_iso, period="day")
            h_str = f"{val_h:.0f}" if val_h is not None else "n/a"
            d_str = f"{val_d:.0f}" if val_d is not None else "n/a"
            marker = ""
            if val_h is not None and reported is not None and abs(val_h - reported) <= 1:
                marker = " ✓"
            elif val_d is not None and reported is not None and abs(val_d - reported) <= 1:
                marker = ""  # mark on the day col below
            print(f"  {ename:>10} {h_str:>8} {d_str:>8}{marker}")
        print()


def av_status(ha: HAClient) -> None:
    """Show current P110 smart plug reading (AV stack)."""
    power = ha.get_state(AV_POWER)
    today = ha.get_state(AV_ENERGY_TODAY)
    month = ha.get_state(AV_ENERGY_MONTH)

    print("TP-Link P110 — AV stack (TV + Marantz + Apple TV + WiiM)")
    print()
    if power:
        print(f"  Current power draw:    {power['state']:>8} {power['attributes'].get('unit_of_measurement', '')}")
    else:
        print("  Current power draw:    (unavailable)")
    if today:
        print(f"  Today's consumption:   {today['state']:>8} {today['attributes'].get('unit_of_measurement', '')}")
    if month:
        print(f"  This month's consump.: {month['state']:>8} {month['attributes'].get('unit_of_measurement', '')}")


def circuit_report(ha: HAClient, start_dt: datetime, end_dt: datetime, daily: bool = False) -> None:
    """Show billing period breakdown: heat pump + AV stack (P110).

    Once the P110 has accumulated historical data, this will show the
    breakdown for any billing period.
    """
    print(f"Period:  {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}")
    days = (end_dt - start_dt).days + 1
    print()

    # Heat pump (from Daikin Antlia statistics)
    hp_kwh = compute_consumption_smart(ha, start_dt, end_dt)

    # AV stack (from P110 energy sensor statistics)
    # The P110 today's consumption resets daily; try monthly first.
    av_kwh = None
    for entity in (AV_ENERGY_MONTH, AV_ENERGY_TODAY):
        av_kwh = ha.get_energy_consumption(
            entity, start_dt.isoformat(), end_dt.isoformat()
        )
        if av_kwh is not None:
            break

    hp_str = f"{hp_kwh:.1f} kWh" if hp_kwh is not None else "n/a"
    av_str = f"{av_kwh:.1f} kWh" if av_kwh is not None else "n/a (no historical data yet)"

    print(f"  Heat pump (Antlia):       {hp_str}")
    print(f"  AV stack (P110):          {av_str}")

    if hp_kwh is not None and av_kwh is not None:
        total = hp_kwh + av_kwh
        print(f"  ────────────────────────────────")
        print(f"  Measured total:           {total:.1f} kWh")
        print(f"  Daily average:            {total / days:.1f} kWh/day  ({days} days)")
    elif hp_kwh is not None:
        print(f"  Daily avg (HP only):      {hp_kwh / days:.1f} kWh/day  ({days} days)")

    if daily:
        has_av = av_kwh is not None
        header = f"{'Date':<12} {'HP':>6}" + (f" {'AV':>6} {'Total':>7}" if has_av else "")
        print(f"\n{header}")
        print("-" * len(header))
        current = start_dt
        while current.date() <= end_dt.date():
            day_start = current.replace(hour=0, minute=0, second=0)
            day_end = current.replace(hour=23, minute=59, second=59)
            d_hp = compute_consumption_smart(ha, day_start, day_end)
            hp_v = f"{d_hp:.1f}" if d_hp is not None else "n/a"
            line = f"{current.strftime('%Y-%m-%d'):<12} {hp_v:>6}"
            if has_av:
                d_av = None
                for entity in (AV_ENERGY_MONTH, AV_ENERGY_TODAY):
                    d_av = ha.get_energy_consumption(
                        entity, day_start.isoformat(), day_end.isoformat()
                    )
                    if d_av is not None:
                        break
                a_v = f"{d_av:.1f}" if d_av is not None else "n/a"
                t_v = f"{d_hp + d_av:.1f}" if d_hp is not None and d_av is not None else "n/a"
                line += f" {a_v:>6} {t_v:>7}"
            print(line)
            current += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Heat pump consumption for arbitrary periods")
    parser.add_argument("--discover", action="store_true",
                        help="List available antlia statistic IDs")
    parser.add_argument("--probe", type=str, metavar="ENTITY_ID",
                        help="Probe a specific entity's statistics shape")
    parser.add_argument("--probe-days", type=int, default=3,
                        help="Number of days to probe (default: 3)")
    parser.add_argument("--verify", action="store_true",
                        help="Cross-check calculated vs reported consumption")
    parser.add_argument("--circuit", action="store_true",
                        help="Show breakdown (heat pump + AV stack via P110)")
    parser.add_argument("--start", type=str,
                        help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", type=str,
                        help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--daily", action="store_true",
                        help="Show daily breakdown")
    args = parser.parse_args()

    ha = get_client()

    if args.discover:
        discover(ha)
        return

    if args.probe:
        probe(ha, args.probe, args.probe_days)
        return

    if args.verify:
        verify(ha)
        return

    if args.circuit and not (args.start and args.end):
        av_status(ha)
        return

    if args.start and args.end:
        # Use Europe/Athens for local billing dates
        tz = ZoneInfo("Europe/Athens")
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=tz)
        # End is inclusive: go to end of that day
        end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(
            tzinfo=tz, hour=23, minute=59, second=59
        )

        if args.circuit:
            circuit_report(ha, start_dt, end_dt, daily=args.daily)
            return

        print(f"Period:  {args.start} → {args.end}")
        print()

        kwh = compute_consumption_smart(ha, start_dt, end_dt)
        if kwh is not None:
            print(f"Total consumption: {kwh:.1f} kWh")
            days = (end_dt - start_dt).days + 1
            print(f"Daily average:     {kwh / days:.1f} kWh/day  ({days} days)")

        # Daily breakdown
        if args.daily:
            print(f"\n{'Date':<12} {'kWh':>6}")
            print("-" * 20)
            current = start_dt
            while current.date() <= end_dt.date():
                day_start = current.replace(hour=0, minute=0, second=0)
                day_end = current.replace(hour=23, minute=59, second=59)
                day_kwh = compute_consumption_smart(ha, day_start, day_end)
                val = f"{day_kwh:.1f}" if day_kwh is not None else "n/a"
                print(f"{current.strftime('%Y-%m-%d'):<12} {val:>6}")
                current += timedelta(days=1)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
