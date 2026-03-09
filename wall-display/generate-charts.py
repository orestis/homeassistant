#!/usr/bin/env python3
"""Generate charts and HTML table from WD correction analysis CSV."""

import csv
from datetime import datetime

rows = []
with open('wd-correction-analysis.csv') as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)

print(f"Loaded {len(rows)} rows")

# --- Charts via matplotlib ---
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

timestamps = [datetime.strptime(r['timestamp'], '%Y-%m-%d %H:%M') for r in rows]
antlia = [float(r['antlia_temp']) for r in rows]
daikin = [float(r['daikin_ap_temp']) for r in rows]
lwt_antlia = [float(r['lwt_from_antlia']) for r in rows]
lwt_daikin = [float(r['lwt_from_daikin']) for r in rows]
lwt_corrected = [float(r['effective_lwt_corrected']) for r in rows]
correction = [int(r['solar_correction_deadband']) for r in rows]

# --- Figure 1: Time series ---
fig, axes = plt.subplots(3, 1, figsize=(16, 14), sharex=True)
fig.suptitle('WD Curve Solar Correction Analysis\nDec 2025 – Mar 2026', fontsize=14, fontweight='bold')

ax1 = axes[0]
ax1.plot(timestamps, antlia, color='#E53935', alpha=0.7, linewidth=0.8, label='Antlia (heat pump sensor)')
ax1.plot(timestamps, daikin, color='#1E88E5', alpha=0.7, linewidth=0.8, label='Daikin AP (sheltered)')
ax1.fill_between(timestamps, antlia, daikin, where=[a > d for a, d in zip(antlia, daikin)],
                  color='#FFAB91', alpha=0.3, label='Solar gain (Antlia > Daikin)')
ax1.fill_between(timestamps, antlia, daikin, where=[a <= d for a, d in zip(antlia, daikin)],
                  color='#90CAF9', alpha=0.3)
ax1.set_ylabel('Outdoor Temp (°C)')
ax1.legend(loc='upper left', fontsize=8)
ax1.grid(True, alpha=0.3)
ax1.set_title('Outdoor Temperature Readings', fontsize=10)

ax2 = axes[1]
ax2.plot(timestamps, lwt_antlia, color='#E53935', alpha=0.6, linewidth=0.8, label='LWT from Antlia (what HP does)')
ax2.plot(timestamps, lwt_daikin, color='#1E88E5', alpha=0.6, linewidth=0.8, label='LWT from Daikin AP (ideal)')
ax2.plot(timestamps, lwt_corrected, color='#43A047', alpha=0.8, linewidth=1.0, label='LWT corrected (with offset)')
ax2.fill_between(timestamps, lwt_antlia, lwt_daikin, where=[la < ld for la, ld in zip(lwt_antlia, lwt_daikin)],
                  color='#FFAB91', alpha=0.3, label='Under-heating error')
ax2.set_ylabel('Leaving Water Temp (°C)')
ax2.legend(loc='upper left', fontsize=8)
ax2.grid(True, alpha=0.3)
ax2.set_title('Leaving Water Temperature: Actual vs Ideal vs Corrected', fontsize=10)

ax3 = axes[2]
ax3.bar(timestamps, correction, width=0.04, color='#FF8F00', alpha=0.8, label='Offset correction applied')
ax3.axhline(y=0, color='gray', linewidth=0.5)
ax3.axhline(y=10, color='red', linewidth=0.5, linestyle='--', alpha=0.5, label='Max offset (+10)')
ax3.set_ylabel('Offset Correction (°C)')
ax3.set_xlabel('Date')
ax3.legend(loc='upper left', fontsize=8)
ax3.grid(True, alpha=0.3)
ax3.set_title('Solar Correction Applied (2.0°C deadband, positive-only)', fontsize=10)

ax3.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
ax3.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig('wd-correction-chart.svg', format='svg', bbox_inches='tight')
plt.savefig('wd-correction-chart.png', format='png', dpi=150, bbox_inches='tight')
print("Saved wd-correction-chart.svg and .png")

# --- Figure 2: Scatter ---
fig2, ax = plt.subplots(figsize=(10, 7))
ax.set_title('WD Curve: Outdoor Temp → LWT\nActual vs Corrected', fontsize=12, fontweight='bold')

x_line = np.linspace(-5, 30, 100)
y_line = 53.125 - 1.5625 * x_line
ax.plot(x_line, y_line, 'k-', linewidth=2, label='WD Curve (50@2, 25@18)', zorder=5)

ax.scatter(antlia, lwt_antlia, s=3, c='#E53935', alpha=0.3, label='HP actual (Antlia sensor)')
ax.scatter(daikin, lwt_daikin, s=3, c='#1E88E5', alpha=0.3, label='Ideal (Daikin AP sensor)')

active_x = [antlia[i] for i in range(len(rows)) if correction[i] > 0]
active_y = [lwt_corrected[i] for i in range(len(rows)) if correction[i] > 0]
ax.scatter(active_x, active_y, s=15, c='#43A047', alpha=0.7, marker='D', label='Corrected (when active)', zorder=4)

ax.set_xlabel('Outdoor Temperature (°C)')
ax.set_ylabel('Leaving Water Temperature (°C)')
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)
ax.set_xlim(-2, 28)
ax.set_ylim(20, 60)
plt.tight_layout()
plt.savefig('wd-correction-scatter.svg', format='svg', bbox_inches='tight')
plt.savefig('wd-correction-scatter.png', format='png', dpi=150, bbox_inches='tight')
print("Saved wd-correction-scatter.svg and .png")

plt.close('all')

# --- HTML table ---
def temp_color(val, vmin=-3, vmax=10):
    if val <= 0:
        t = max(val / vmin, 0) if vmin != 0 else 0
        return f'rgba(33,150,243,{t*0.4:.2f})'
    else:
        t = min(val / vmax, 1) if vmax != 0 else 0
        return f'rgba(255,143,0,{t*0.6:.2f})'

def lwt_error_color(val):
    if val <= 0:
        return ''
    t = min(val / 15, 1)
    return f'rgba(229,57,53,{t*0.5:.2f})'

total = len(rows)
active_count = sum(1 for r in rows if int(r['solar_correction_deadband']) > 0)
active_corrections = [int(r['solar_correction_deadband']) for r in rows if int(r['solar_correction_deadband']) > 0]
lwt_errors = [float(r['lwt_error']) for r in rows]
max_error = max(lwt_errors)
mean_corr = sum(active_corrections) / len(active_corrections) if active_corrections else 0

html = f'''<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>WD Correction Analysis</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 20px; background: #1a1a2e; color: #e0e0e0; }}
  h1 {{ color: #fff; }}
  .summary {{ background: #16213e; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
  .summary h2 {{ margin-top: 0; color: #64b5f6; }}
  .stats {{ display: flex; gap: 20px; flex-wrap: wrap; }}
  .stat {{ background: #0f3460; padding: 12px 20px; border-radius: 6px; text-align: center; }}
  .stat .val {{ font-size: 24px; font-weight: bold; color: #fff; }}
  .stat .label {{ font-size: 12px; color: #90a4ae; margin-top: 4px; }}
  .charts {{ display: flex; flex-wrap: wrap; gap: 20px; margin: 20px 0; }}
  .charts img {{ max-width: 100%; border-radius: 8px; background: #fff; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th {{ background: #0f3460; color: #64b5f6; padding: 8px 6px; position: sticky; top: 0; text-align: right; cursor: pointer; }}
  th:first-child, th:nth-child(2), th:nth-child(3) {{ text-align: left; }}
  td {{ padding: 5px 6px; border-bottom: 1px solid #1a1a3e; text-align: right; font-variant-numeric: tabular-nums; }}
  td:first-child, td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
  tr:hover {{ background: #16213e; }}
  .correction-active {{ background: rgba(255,143,0,0.12); }}
  .legend {{ display: flex; gap: 15px; margin: 10px 0; font-size: 13px; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 5px; }}
  .legend-swatch {{ width: 16px; height: 16px; border-radius: 3px; display: inline-block; }}
  .filter-bar {{ margin: 15px 0; display: flex; gap: 20px; align-items: center; }}
  .filter-bar label {{ cursor: pointer; }}
  .filter-bar input[type="checkbox"] {{ cursor: pointer; }}
</style>
</head><body>
<h1>🌡️ WD Curve Solar Correction Analysis</h1>
<p>Dec 2025 – Mar 2026 · {total:,} hourly readings · WD Curve: 50°C @ 2°C → 25°C @ 18°C · Deadband: 2.0°C positive-only</p>

<div class="summary">
<h2>Summary</h2>
<div class="stats">
  <div class="stat"><div class="val">{total:,}</div><div class="label">Total Hours</div></div>
  <div class="stat"><div class="val">{active_count}</div><div class="label">Hours Corrected ({100*active_count/total:.1f}%)</div></div>
  <div class="stat"><div class="val">+{mean_corr:.1f}°C</div><div class="label">Mean Correction (active)</div></div>
  <div class="stat"><div class="val">+{max(active_corrections)}°C</div><div class="label">Max Correction</div></div>
  <div class="stat"><div class="val">+{max_error:.1f}°C</div><div class="label">Max LWT Error</div></div>
</div></div>

<h2>Charts</h2>
<div class="charts">
  <img src="wd-correction-chart.png" alt="Time series chart">
  <img src="wd-correction-scatter.png" alt="Scatter plot">
</div>

<h2>Data Table</h2>
<div class="legend">
  <div class="legend-item"><span class="legend-swatch" style="background:rgba(255,143,0,0.4)"></span> Temp delta (orange = solar gain)</div>
  <div class="legend-item"><span class="legend-swatch" style="background:rgba(229,57,53,0.4)"></span> LWT error (red = under-heating)</div>
  <div class="legend-item"><span class="legend-swatch" style="background:rgba(67,160,71,0.3)"></span> Corrected LWT (green = correction active)</div>
  <div class="legend-item"><span class="legend-swatch" style="background:rgba(255,143,0,0.12)"></span> Row highlighted when correction active</div>
</div>

<div class="filter-bar">
  <label><input type="checkbox" id="onlyActive" onchange="filterRows()"> Show only corrected hours</label>
  <span id="rowCount" style="color:#90a4ae; font-size:13px;">Showing {total:,} rows</span>
</div>

<table id="dataTable">
<thead><tr>
  <th>Timestamp</th><th>Day</th><th>Hr</th>
  <th>Antlia °C</th><th>Daikin °C</th><th>Δ Temp</th>
  <th>LWT (HP)</th><th>LWT (Ideal)</th><th>LWT Error</th>
  <th>Correction</th><th>Offset</th>
  <th>LWT Uncorr.</th><th>LWT Corr.</th>
</tr></thead>
<tbody>
'''

for r in rows:
    corr = int(r['solar_correction_deadband'])
    d = float(r['temp_delta'])
    err = float(r['lwt_error'])
    row_class = ' class="correction-active"' if corr > 0 else ''

    html += f'<tr{row_class} data-active="{1 if corr > 0 else 0}">'
    html += f'<td>{r["timestamp"]}</td>'
    html += f'<td>{r["weekday"]}</td>'
    html += f'<td>{r["hour"]}</td>'
    html += f'<td>{float(r["antlia_temp"]):.1f}</td>'
    html += f'<td>{float(r["daikin_ap_temp"]):.1f}</td>'
    html += f'<td style="background:{temp_color(d)}">{d:+.1f}</td>'
    html += f'<td>{float(r["lwt_from_antlia"]):.1f}</td>'
    html += f'<td>{float(r["lwt_from_daikin"]):.1f}</td>'
    html += f'<td style="background:{lwt_error_color(err)}">{err:+.1f}</td>'
    html += f'<td style="background:{temp_color(corr, vmax=15)}">{"+" + str(corr) if corr > 0 else "—"}</td>'
    html += f'<td>{"+" + str(int(r["base_offset_0_final"])) if corr > 0 else "0"}</td>'
    html += f'<td>{float(r["effective_lwt_antlia"]):.1f}</td>'
    html += f'<td style="background:{"rgba(67,160,71,0.2)" if corr > 0 else ""}">{float(r["effective_lwt_corrected"]):.1f}</td>'
    html += '</tr>\n'

html += f'''</tbody></table>

<script>
function filterRows() {{
  const onlyActive = document.getElementById('onlyActive').checked;
  const rows = document.querySelectorAll('#dataTable tbody tr');
  let shown = 0;
  rows.forEach(row => {{
    if (onlyActive && row.dataset.active === '0') {{
      row.style.display = 'none';
    }} else {{
      row.style.display = '';
      shown++;
    }}
  }});
  document.getElementById('rowCount').textContent = 'Showing ' + shown.toLocaleString() + ' rows';
}}
</script>
</body></html>'''

with open('wd-correction-analysis.html', 'w') as f:
    f.write(html)
print(f"Saved wd-correction-analysis.html ({len(rows)} rows)")
