"""
Download hourly PM2.5 and PM10 data for Belgrade air quality stations
from the OpenAQ API (v3) and save it as CSV files.

Usage (from the project root folder):
    python src/fetch_data.py

Requires an OpenAQ API key in a .env file:
    OPENAQ_API_KEY=your_key_here
"""

import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

BASE_URL = "https://api.openaq.org/v3"

# OpenAQ location IDs (source: EEA, reference-grade stations)
STATIONS = {
    10879: "Novi Beograd",
    10837: "Stari grad",
    11587: "Mostar",
    11588: "Vracar",
    784164: "Obrenovac Centar",
}

PARAMETERS = ["pm25", "pm10"]  # pollutants we want
START_YEAR = 2017
END_YEAR = datetime.now(timezone.utc).year

PAGE_LIMIT = 1000        # max rows per request
SLEEP_SECONDS = 1.1      # free tier: 60 requests per minute
OUTPUT_DIR = os.path.join("data", "raw")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def get_headers():
    """Read the API key from the .env file."""
    load_dotenv()
    api_key = os.getenv("OPENAQ_API_KEY")
    if not api_key:
        raise SystemExit("OPENAQ_API_KEY not found. Add it to your .env file.")
    return {"X-API-Key": api_key}


def api_get(url, headers, params=None, retries=5):
    """GET request with retries for rate limits (429), server errors (5xx)
    and network problems. Returns None if every attempt fails."""
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
        except requests.exceptions.RequestException as e:
            wait = 5 * attempt
            print(f"  Network error ({e.__class__.__name__}), retry {attempt}/{retries} in {wait}s...")
            time.sleep(wait)
            continue

        if response.status_code == 429:
            print("  Rate limit reached, waiting 60 seconds...")
            time.sleep(60)
            continue
        if response.status_code >= 500:
            wait = 5 * attempt
            print(f"  Server error {response.status_code}, retry {attempt}/{retries} in {wait}s...")
            time.sleep(wait)
            continue

        response.raise_for_status()  # 4xx errors (bad key, bad ID) should stop the script
        time.sleep(SLEEP_SECONDS)
        return response.json()

    print(f"  WARNING: request failed after {retries} attempts, skipping: {url} {params or ''}")
    return None


def get_sensors(location_id, headers):
    """Return {parameter_name: sensor_id} for the pollutants we need."""
    data = api_get(f"{BASE_URL}/locations/{location_id}/sensors", headers)
    if data is None:
        return {}
    sensors = {}
    for sensor in data.get("results", []):
        name = sensor["parameter"]["name"]
        if name in PARAMETERS:
            sensors[name] = sensor["id"]
    return sensors


def get_hourly_data(sensor_id, year, headers):
    """Download all hourly values for one sensor and one year.

    The year is fetched month by month: a month has at most ~744 hours,
    so it fits in one page. Deep paging (page=5, 6, ...) on a whole year
    is what makes the OpenAQ API return 500 errors.
    """
    rows = []
    now = datetime.now(timezone.utc)

    for month in range(1, 13):
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if start > now:  # no data from the future
            break
        end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)

        page = 1
        while True:
            params = {
                "datetime_from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "datetime_to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": PAGE_LIMIT,
                "page": page,
            }
            data = api_get(f"{BASE_URL}/sensors/{sensor_id}/hours", headers, params)
            if data is None:  # failed even after retries -> skip this month
                break
            results = data.get("results", [])

            for r in results:
                rows.append({
                    "datetime_utc": r["period"]["datetimeFrom"]["utc"],
                    "value": r["value"],
                })

            if len(results) < PAGE_LIMIT:  # last page
                break
            page += 1
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    headers = get_headers()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_data = []

    for location_id, station in STATIONS.items():
        print(f"\n{station} (location {location_id})")
        sensors = get_sensors(location_id, headers)
        print(f"  Sensors found: {sensors}")

        for parameter, sensor_id in sensors.items():
            station_rows = []
            for year in range(START_YEAR, END_YEAR + 1):
                rows = get_hourly_data(sensor_id, year, headers)
                print(f"  {parameter} {year}: {len(rows)} rows")
                station_rows.extend(rows)

            df = pd.DataFrame(station_rows)
            if df.empty:
                continue
            df["station"] = station
            df["location_id"] = location_id
            df["parameter"] = parameter

            file_name = f"{station.lower().replace(' ', '_')}_{parameter}.csv"
            df.to_csv(os.path.join(OUTPUT_DIR, file_name), index=False)
            all_data.append(df)

    if all_data:
        combined = pd.concat(all_data, ignore_index=True)
        combined_path = os.path.join(OUTPUT_DIR, "belgrade_air_quality_raw.csv")
        combined.to_csv(combined_path, index=False)
        print(f"\nDone! {len(combined):,} rows saved to {combined_path}")
    else:
        print("\nNo data downloaded. Check the station IDs and your API key.")


if __name__ == "__main__":
    main()
