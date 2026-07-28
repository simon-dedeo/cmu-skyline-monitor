#!/usr/bin/env python3
"""
solar.py — Sunrise, sunset, and golden hour calculation for Pittsburgh.

Uses a simplified solar position algorithm (accurate to ~1 minute).
All times returned in UTC.
"""

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/New_York")

# Pittsburgh coordinates
LATITUDE = 40.4406
LONGITUDE = -79.9959


def _julian_day(dt):
    """Convert datetime to Julian day number."""
    a = (14 - dt.month) // 12
    y = dt.year + 4800 - a
    m = dt.month + 12 * a - 3
    jdn = dt.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    return jdn + (dt.hour - 12) / 24.0 + dt.minute / 1440.0 + dt.second / 86400.0


def _solar_declination(day_of_year):
    """Solar declination in radians."""
    return math.radians(-23.44) * math.cos(math.radians((360 / 365.0) * (day_of_year + 10)))


def _equation_of_time(day_of_year):
    """Equation of time in minutes."""
    b = math.radians((360 / 365.0) * (day_of_year - 81))
    return 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)


def _hour_angle(declination, latitude_rad, elevation_angle):
    """
    Hour angle for when sun crosses a given elevation.
    elevation_angle in degrees: 0 = geometric horizon, -0.833 = standard refraction.
    Returns hours from solar noon, or None if sun doesn't reach that elevation.
    """
    lat = latitude_rad
    dec = declination
    el = math.radians(elevation_angle)

    cos_ha = (math.sin(el) - math.sin(lat) * math.sin(dec)) / (math.cos(lat) * math.cos(dec))

    if cos_ha < -1 or cos_ha > 1:
        return None  # sun never reaches this elevation

    ha = math.acos(cos_ha)
    return math.degrees(ha) / 15.0  # convert to hours


def sun_elevation(dt=None):
    """Sun elevation (altitude) above the horizon in degrees for Pittsburgh at UTC
    datetime dt. Negative = below the horizon. Uses the same simplified model as
    sun_times (declination + equation of time), good to ~0.1-0.2 deg."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    year_start = datetime(dt.year, 1, 1, tzinfo=timezone.utc)
    day_of_year = (dt - year_start).days + 1
    lat = math.radians(LATITUDE)
    dec = _solar_declination(day_of_year)
    eot = _equation_of_time(day_of_year)
    utc_hours = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    solar_time = utc_hours + eot / 60.0 + LONGITUDE / 15.0     # LONGITUDE is negative
    hour_angle = math.radians(15.0 * (solar_time - 12.0))
    sin_el = (math.sin(lat) * math.sin(dec)
              + math.cos(lat) * math.cos(dec) * math.cos(hour_angle))
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))


def sun_times(dt=None):
    """
    Calculate sunrise, sunset, and golden hour times for Pittsburgh.

    Returns dict with UTC datetime objects:
        sunrise, sunset,
        golden_morning_start, golden_morning_end (= sunrise),
        golden_evening_start (= sunset), golden_evening_end,
        golden_morning_mid, golden_evening_mid

    Golden hour is defined as sun between -4° and +6° elevation.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    # Key the calendar date off LOCAL (Pittsburgh) time, not UTC: during DST the
    # evening golden window spans 00:00 UTC, so a UTC-keyed date flips every field
    # to the next day mid-window (2026-07-13: goldtick finalized the evening window
    # at 00:05Z = 8:05 PM EDT, an hour early, and stopped scanning through sunset).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(LOCAL_TZ)
    day_of_year = local.timetuple().tm_yday

    lat_rad = math.radians(LATITUDE)
    declination = _solar_declination(day_of_year)
    eot = _equation_of_time(day_of_year)

    # Solar noon in UTC
    # solar_noon = 12:00 - equation_of_time - longitude/15
    solar_noon_hours = 12.0 - eot / 60.0 - LONGITUDE / 15.0
    solar_noon = datetime(local.year, local.month, local.day, tzinfo=timezone.utc) + \
        timedelta(hours=solar_noon_hours)

    result = {"solar_noon": solar_noon}

    # Sunrise/sunset: sun at -0.833° (standard refraction correction)
    ha_rise = _hour_angle(declination, lat_rad, -0.833)
    if ha_rise is not None:
        result["sunrise"] = solar_noon - timedelta(hours=ha_rise)
        result["sunset"] = solar_noon + timedelta(hours=ha_rise)

    # Golden hour boundaries
    # Morning golden hour: sun goes from -4° to +6°
    # Evening golden hour: sun goes from +6° to -4°
    ha_minus4 = _hour_angle(declination, lat_rad, -4.0)
    ha_plus6 = _hour_angle(declination, lat_rad, 15.0) ## shift this a little for AM

    if ha_minus4 is not None:
        result["golden_morning_start"] = solar_noon - timedelta(hours=ha_minus4)
        result["golden_evening_end"] = solar_noon + timedelta(hours=ha_minus4)

    if ha_plus6 is not None:
        result["golden_morning_end"] = solar_noon - timedelta(hours=ha_plus6)
        result["golden_evening_start"] = solar_noon + timedelta(hours=ha_plus6)

    # Midpoints of golden hours
    if "golden_morning_start" in result and "golden_morning_end" in result:
        gm_start = result["golden_morning_start"]
        gm_end = result["golden_morning_end"]
        result["golden_morning_mid"] = gm_start + (gm_end - gm_start) / 2

    if "golden_evening_start" in result and "golden_evening_end" in result:
        ge_start = result["golden_evening_start"]
        ge_end = result["golden_evening_end"]
        result["golden_evening_mid"] = ge_start + (ge_end - ge_start) / 2

    return result


def next_golden_hours(dt=None):
    """
    Return the next upcoming golden hour midpoints (morning and evening).
    Checks today and tomorrow to always return future times.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    results = []

    for day_offset in range(2):
        check_date = dt + timedelta(days=day_offset)
        times = sun_times(check_date)

        for key in ["golden_morning_mid", "golden_evening_mid"]:
            if key in times and times[key] > dt:
                results.append((key, times[key]))

    results.sort(key=lambda x: x[1])
    return results


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    times = sun_times(now)

    print(f"Date: {now.strftime('%Y-%m-%d')} (UTC)")
    print()

    for key in ["sunrise", "solar_noon", "sunset",
                 "golden_morning_start", "golden_morning_mid", "golden_morning_end",
                 "golden_evening_start", "golden_evening_mid", "golden_evening_end"]:
        if key in times:
            t = times[key]
            # Also show EST/EDT
            est = t - timedelta(hours=5)
            edt = t - timedelta(hours=4)
            print(f"  {key:30s}  {t.strftime('%H:%M:%S')} UTC  ({edt.strftime('%H:%M')} EDT / {est.strftime('%H:%M')} EST)")

    print()
    print("Next golden hours:")
    for name, t in next_golden_hours(now):
        print(f"  {name}: {t.strftime('%Y-%m-%d %H:%M:%S')} UTC")
