#!/usr/bin/env /opt/local/bin/python3.11
"""
update.py — data generator for the CMU Courtyard Monitor dashboard.

Runs on the Laboratory for Social Minds MacBook Pro. Each invocation:
  1. Captures a fresh frame from each Arducam (courtyard + sky).
  2. Pulls current weather + forecast from the US National Weather Service.
  3. Computes sun / golden-hour times via the existing solar.py.
  4. Reads a few system-health numbers.
  5. Writes everything to data.json next to dashboard.html.

Pure standard library + imagesnap (MacPorts). No pip deps required.
Designed to fail soft: a network or camera hiccup never aborts the run;
the previous images/fields simply persist and an 'errors' list is recorded.
"""

import json, os, sys, subprocess, ssl, time, urllib.request, socket
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
# Prefer the solar.py bundled next to this script. ~/Desktop/files is a
# fallback but is TCC-protected from launchd, so we ship our own copy.
sys.path.insert(0, os.path.expanduser("~/Desktop/files"))
sys.path.insert(0, HERE)

IMAGESNAP = "/opt/local/bin/imagesnap"
UA = "lsm-courtyard-monitor (sdedeo@andrew.cmu.edu)"

# NWS grid for CMU (40.4443, -79.9436) -> office PBZ, grid 79,67
NWS = "https://api.weather.gov/gridpoints/PBZ/79,67"
# Current conditions geolocated to CMU's exact lat/lon (Open-Meteo interpolates to
# the point — elevation 282 m, the Oakland hilltop — vs NWS's nearest station KAGC,
# an airport ~10 mi south in a river valley). No API key needed.
CMU_LAT, CMU_LON = 40.4443, -79.9436
OPEN_METEO = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={CMU_LAT}&longitude={CMU_LON}"
    "&current=temperature_2m,relative_humidity_2m,apparent_temperature,dew_point_2m,"
    "is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m,cloud_cover"
    "&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=America%2FNew_York"
)
# WMO weather-code -> label (Open-Meteo's current.weather_code)
WMO = {0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
       45: "Fog", 48: "Rime Fog", 51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
       56: "Freezing Drizzle", 57: "Freezing Drizzle", 61: "Light Rain", 63: "Rain",
       65: "Heavy Rain", 66: "Freezing Rain", 67: "Freezing Rain", 71: "Light Snow",
       73: "Snow", 75: "Heavy Snow", 77: "Snow Grains", 80: "Light Showers", 81: "Showers",
       82: "Violent Showers", 85: "Snow Showers", 86: "Snow Showers", 95: "Thunderstorm",
       96: "Thunderstorm w/ Hail", 99: "Thunderstorm w/ Hail"}
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW",
           "W", "WNW", "NW", "NNW"]
# camera name (see expose.py) -> output file
CAMERAS = {"courtyard": "courtyard.jpg", "sky": "sky.jpg"}

errors = []
cam_meta = {}


def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.load(r)


def snap(name, outfile):
    """Capture one frame via expose.py (controlled sky / auto courtyard).
    Writes to a temp file first; leaves the old frame in place on failure."""
    import expose
    tmp = os.path.join(HERE, outfile + ".tmp.jpg")
    try:
        meta = expose.capture(name, tmp)
        os.replace(tmp, os.path.join(HERE, outfile))
        cam_meta[name] = meta
    except Exception as e:
        errors.append(f"camera[{name}]: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)


def c_to_f(c):
    return None if c is None else round(c * 9 / 5 + 32)


def weather():
    out = {}
    # Multi-day forecast (day/night periods)
    try:
        f = http_json(f"{NWS}/forecast")["properties"]["periods"]
        out["forecast"] = [{
            "name": p["name"], "temp": p["temperature"], "unit": p["temperatureUnit"],
            "short": p["shortForecast"], "icon": p.get("icon", ""),
            "isDay": p["isDaytime"], "wind": p["windSpeed"], "windDir": p["windDirection"],
            "detail": p["detailedForecast"],
        } for p in f[:8]]
    except Exception as e:
        errors.append(f"forecast: {e}")
    # Hourly (next 12h) for the strip
    try:
        h = http_json(f"{NWS}/forecast/hourly")["properties"]["periods"][:12]
        out["hourly"] = [{
            "time": p["startTime"], "temp": p["temperature"], "unit": p["temperatureUnit"],
            "short": p["shortForecast"], "pop": (p.get("probabilityOfPrecipitation") or {}).get("value"),
        } for p in h]
    except Exception as e:
        errors.append(f"hourly: {e}")
    # Current conditions GEOLOCATED TO CMU (Open-Meteo, exact lat/lon).
    def compass(deg):
        return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16] if deg is not None else None
    try:
        om = http_json(OPEN_METEO)["current"]
        out["current"] = {
            "source": "open-meteo @ CMU",
            "text": WMO.get(om.get("weather_code"), "—"),
            "tempF": round(om["temperature_2m"]),
            "feelsF": round(om["apparent_temperature"]),
            "dewF": round(om["dew_point_2m"]),
            "humidity": round(om["relative_humidity_2m"]),
            "windMph": round(om["wind_speed_10m"]),
            "windDir": compass(om.get("wind_direction_10m")),
            "cloud": om.get("cloud_cover"),
            "precip": om.get("precipitation"),
            "isDay": bool(om.get("is_day")),
            "time": om.get("time"),
        }
    except Exception as e:
        errors.append(f"open-meteo: {e}")
        # Fallback: nearest NWS station observation
        try:
            sid = http_json(f"{NWS}/stations")["features"][0]["properties"]["stationIdentifier"]
            obs = http_json(f"https://api.weather.gov/stations/{sid}/observations/latest")["properties"]
            out["current"] = {
                "source": f"nws @ {sid}", "text": obs.get("textDescription"),
                "tempF": c_to_f((obs.get("temperature") or {}).get("value")),
                "dewF": c_to_f((obs.get("dewpoint") or {}).get("value")),
                "humidity": round(v) if (v := (obs.get("relativeHumidity") or {}).get("value")) is not None else None,
                "windMph": round(w * 0.621371) if (w := (obs.get("windSpeed") or {}).get("value")) is not None else None,
                "time": obs.get("timestamp"),
            }
        except Exception as e2:
            errors.append(f"nws-fallback: {e2}")
    return out


def solar():
    try:
        import solar as S
        now = datetime.now(timezone.utc)
        t = S.sun_times(now)
        keys = ["sunrise", "solar_noon", "sunset",
                "golden_morning_start", "golden_morning_mid", "golden_morning_end",
                "golden_evening_start", "golden_evening_mid", "golden_evening_end"]
        out = {k: t[k].isoformat() for k in keys if k in t}
        out["next_golden"] = [{"name": n, "utc": dt.isoformat()} for n, dt in S.next_golden_hours(now)[:3]]
        # daylight length
        if "sunrise" in t and "sunset" in t:
            out["daylight_seconds"] = (t["sunset"] - t["sunrise"]).total_seconds()
        return out
    except Exception as e:
        errors.append(f"solar: {e}")
        return {}


def system():
    out = {}
    try:
        out["uptime"] = subprocess.check_output(["uptime"], text=True).strip()
        la = os.getloadavg()
        out["load"] = [round(x, 2) for x in la]
        out["cpus"] = os.cpu_count()      # for load-as-%-of-CPUs on the dashboard
        df = subprocess.check_output(["df", "-h", "/"], text=True).splitlines()[1].split()
        out["disk"] = {"size": df[1], "used": df[2], "avail": df[3], "capacity": df[4]}
        # day-of-project: days since 2026-01-01 (matches timelapse day-XXX numbering)
        out["project_day"] = (datetime.now(timezone.utc).date() - datetime(2026, 1, 1).date()).days
        out["host"] = socket.gethostname()
    except Exception as e:
        errors.append(f"system: {e}")
    return out


def file_ts(name):
    """UTC ISO timestamp of a file's last write — i.e. the camera's last
    successful capture (snap() leaves the old frame in place on failure,
    so the mtime freezes when a camera stops delivering)."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(os.path.join(HERE, name)),
                                      timezone.utc).isoformat()
    except OSError:
        return None


def main():
    for name, outfile in CAMERAS.items():
        # During a golden-hour window the 2.5-min goldscan job owns the sky camera
        # (capture.sh sets GOLD_SKY_REUSE): reuse its frame + meta instead of adding
        # a second multi-exposure capture — unless the frame is stale (goldscan
        # failing), in which case fall through and capture normally.
        if name == "sky" and os.environ.get("GOLD_SKY_REUSE"):
            sky_path = os.path.join(HERE, outfile)
            try:
                age = time.time() - os.path.getmtime(sky_path)
            except OSError:
                age = 1e9
            if age <= 300:
                try:
                    cam_meta[name] = json.load(open(os.path.join(HERE, ".last_gold_meta.json")))
                except Exception:
                    cam_meta[name] = {"mode": "golden-reuse"}
                continue
        snap(name, outfile)

    data = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "generated_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "location": "Carnegie Mellon University · Pittsburgh, PA",
        "weather": weather(),
        "solar": solar(),
        "system": system(),
        "cameras": {
            "courtyard": {"file": "courtyard.jpg", "label": "Courtyard · Arducam 1080P Low Light",
                          "last_capture": file_ts("courtyard.jpg"),
                          **cam_meta.get("courtyard", {})},
            "sky": {"file": "sky.jpg", "label": "Skyline · Arducam B0578 Global Shutter",
                    "last_capture": file_ts("sky.jpg"),
                    **cam_meta.get("sky", {})},
        },
        "errors": errors,
    }
    tmp = os.path.join(HERE, "data.json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, os.path.join(HERE, "data.json"))
    # Also write data.js (JSONP-style) so a cross-origin page (proofsandreasons.io)
    # can load it via <script> — santafe serves no CORS header, so fetch() is blocked.
    tmpjs = os.path.join(HERE, "data.js.tmp")
    with open(tmpjs, "w") as f:
        f.write("window.MONITOR_DATA=" + json.dumps(data) + ";")
    os.replace(tmpjs, os.path.join(HERE, "data.js"))
    print(f"[{data['generated_local']}] wrote data.json ; errors={len(errors)}")
    for e in errors:
        print("  !", e)


if __name__ == "__main__":
    main()
