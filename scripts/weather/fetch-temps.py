#!/usr/bin/env python3
"""
Fetch historical temperature data from Open-Meteo API.

Usage:
    python fetch-temps.py --start 2026-03-13 --end 2026-04-15
    python fetch-temps.py --start 2026-06-01 --end 2026-08-31 --location "New York" --lat 40.7128 --lon -74.0060
    python fetch-temps.py --start 2026-03-13 --end 2026-04-15 --daily  # with daily summary

Locations:
    Athens (default):  lat=37.9838, lon=23.7275
    New York:          lat=40.7128, lon=-74.0060
    London:            lat=51.5074, lon=-0.1278
    Tokyo:             lat=35.6762, lon=139.6503
"""

import argparse
import urllib.request
import json
from datetime import datetime


def get_weather_condition(weather_code):
    """Map WMO weather code to human-readable condition."""
    # WMO Weather interpretation codes
    codes = {
        0: "Clear",
        1: "Mostly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Foggy",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Heavy drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with hail",
        99: "Thunderstorm with hail",
    }
    return codes.get(weather_code, f"Unknown ({weather_code})")


def fetch_temperatures(lat, lon, start_date, end_date, timezone="Europe/Athens"):
    """
    Fetch historical temperature data from Open-Meteo API.

    Args:
        lat (float): Latitude
        lon (float): Longitude
        start_date (str): Start date in YYYY-MM-DD format
        end_date (str): End date in YYYY-MM-DD format
        timezone (str): Timezone for the data

    Returns:
        dict: Temperature data with dates, min, mean, max, conditions, and wind
    """
    url = "https://archive-api.open-meteo.com/v1/archive?latitude={}&longitude={}&start_date={}&end_date={}&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,weather_code,wind_speed_10m_max&timezone={}".format(
        lat, lon, start_date, end_date, timezone.replace(" ", "%20")
    )

    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode())

    return {
        'dates': data['daily']['time'],
        'temp_min': data['daily']['temperature_2m_min'],
        'temp_mean': data['daily']['temperature_2m_mean'],
        'temp_max': data['daily']['temperature_2m_max'],
        'weather_code': data['daily']['weather_code'],
        'wind_speed': data['daily']['wind_speed_10m_max'],
        'lat': lat,
        'lon': lon,
        'timezone': timezone,
    }


def print_temperature_table(data, location_name=""):
    """Print temperature data in a formatted table."""
    dates = data['dates']
    temps_min = data['temp_min']
    temps_mean = data['temp_mean']
    temps_max = data['temp_max']

    location = f" - {location_name}" if location_name else ""
    print(f"Temperature ranges{location}")
    print(f"Period: {dates[0]} to {dates[-1]}\n")
    print("Date          Min    Avg    Max    (°C)")
    print("─" * 40)

    for i, date in enumerate(dates):
        print(f"{date}  {temps_min[i]:5.1f}  {temps_mean[i]:5.1f}  {temps_max[i]:5.1f}")

    # Calculate stats
    avg_min = sum(temps_min) / len(temps_min)
    avg_mean = sum(temps_mean) / len(temps_mean)
    avg_max = sum(temps_max) / len(temps_max)

    print("─" * 40)
    print(f"Period avg:   {avg_min:5.1f}  {avg_mean:5.1f}  {avg_max:5.1f}\n")
    print(f"Highest:  {max(temps_max):5.1f}°C on {dates[temps_max.index(max(temps_max))]}")
    print(f"Lowest:   {min(temps_min):5.1f}°C on {dates[temps_min.index(min(temps_min))]}")
    print(f"Days in period: {len(dates)}")


def print_daily_summary(data, location_name=""):
    """Print daily temperature summary with conditions and wind."""
    dates = data['dates']
    temps_min = data['temp_min']
    temps_mean = data['temp_mean']
    temps_max = data['temp_max']
    weather_codes = data['weather_code']
    wind_speeds = data['wind_speed']

    location = f" - {location_name}" if location_name else ""
    print(f"\nDaily Summary{location}:")
    print("Date          Min    Avg    Max   Wind   Condition")
    print("─" * 70)

    for i, date in enumerate(dates):
        wind = wind_speeds[i]
        condition = get_weather_condition(weather_codes[i])

        print(f"{date}  {temps_min[i]:5.1f}  {temps_mean[i]:5.1f}  {temps_max[i]:5.1f}  {wind:4.1f}  {condition}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical temperature data for consumption correlation analysis"
    )
    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--location",
        type=str,
        default="Athens",
        help="Location name (default: Athens)"
    )
    parser.add_argument(
        "--lat",
        type=float,
        default=37.9838,
        help="Latitude (default: 37.9838 for Athens)"
    )
    parser.add_argument(
        "--lon",
        type=float,
        default=23.7275,
        help="Longitude (default: 23.7275 for Athens)"
    )
    parser.add_argument(
        "--timezone",
        type=str,
        default="Europe/Athens",
        help="Timezone (default: Europe/Athens)"
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Print daily summary with annotations"
    )

    args = parser.parse_args()

    try:
        data = fetch_temperatures(
            args.lat,
            args.lon,
            args.start,
            args.end,
            args.timezone
        )

        print_temperature_table(data, args.location)

        if args.daily:
            print_daily_summary(data, args.location)

    except urllib.error.URLError as e:
        print(f"Error fetching weather data: {e}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
