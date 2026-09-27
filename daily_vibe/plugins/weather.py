"""Weather from Open-Meteo, with MET Norway as a key-free backup.

Location: config [plugins.weather] location =
    ""                 -> auto-detect from IP (ipapi.co, then ip-api.com, then ipwho.is)
    "Denver, CO"       -> geocoded with the Open-Meteo geocoding API
    "39.74,-104.99"    -> explicit latitude,longitude
Units: units = "imperial" (default) or "metric".

Today: current conditions + daily high/low, precipitation, wind.
Past dates: Open-Meteo historical archive (falls back to the forecast API's
recent-past data for the last few days, which the archive doesn't have yet).
Future dates up to ~16 days: daily forecast.

Backup provider: when Open-Meteo fails (HTTP 429 quota, outage, timeout) for
today or a date up to ~9 days ahead, the MET Norway Locationforecast 2.0 API
(api.met.no, free, no key) is used instead. MET's terms require an identifying
User-Agent; we send a generic project UA (MET_USER_AGENT) and append the
optional "contact" setting (email or URL) if you fill it in. MET has no
historical data, so past dates still need Open-Meteo. The provider that
answered is named at the end of the block.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse
import urllib.request

ID = "weather"
NAME = "Weather"
TITLE = "Weather"
VERSION = "1.2.0"
AUTHOR = "The Daily Vibe"
API_VERSION = 1
DESCRIPTION = "Weather for the day from Open-Meteo (MET Norway backup), location from IP or a fixed place."

TIMEOUT = 8
try:
    from daily_vibe import __version__ as _APP_VERSION
except Exception:  # pragma: no cover - plugin loaded standalone
    _APP_VERSION = "0.8.1"
_SHORT_VERSION = ".".join(_APP_VERSION.split(".")[:2])
USER_AGENT = f"daily-vibe/{_SHORT_VERSION}"
MET_USER_AGENT = f"DailyVibe/{_SHORT_VERSION} (The Daily Vibe desktop journal app)"
MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
MET_MAX_DAYS = 9

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# Same models, separate endpoint/quota; covers ~2022..today. Used as fallback.
HIST_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

WMO = {
    0: ("Clear sky", "☀️"), 1: ("Mainly clear", "🌤️"), 2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"), 45: ("Fog", "🌫️"), 48: ("Depositing rime fog", "🌫️"),
    51: ("Light drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Dense drizzle", "🌧️"),
    56: ("Freezing drizzle", "🌧️"), 57: ("Freezing drizzle", "🌧️"),
    61: ("Light rain", "🌦️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
    66: ("Freezing rain", "🌧️"), 67: ("Heavy freezing rain", "🌧️"),
    71: ("Light snow", "🌨️"), 73: ("Snow", "🌨️"), 75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "🌨️"), 80: ("Light showers", "🌦️"), 81: ("Showers", "🌧️"),
    82: ("Violent showers", "⛈️"), 85: ("Snow showers", "🌨️"), 86: ("Heavy snow showers", "❄️"),
    95: ("Thunderstorm", "⛈️"), 96: ("Thunderstorm with hail", "⛈️"), 99: ("Thunderstorm with heavy hail", "⛈️"),
}

_location_cache: dict | None = None  # IP lookup cached for the session


def _get_json(url: str, params: dict | None = None, user_agent: str | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent or USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# Location -------------------------------------------------------------------
def _ip_location() -> dict:
    global _location_cache
    if _location_cache:
        return _location_cache
    errors = []
    providers = [
        ("https://ipapi.co/json/", lambda d: (d["latitude"], d["longitude"], d.get("city"), d.get("region_code") or d.get("region"), d.get("country_name"))),
        ("http://ip-api.com/json/", lambda d: (d["lat"], d["lon"], d.get("city"), d.get("region"), d.get("country"))),
        ("https://ipwho.is/", lambda d: (d["latitude"], d["longitude"], d.get("city"), d.get("region_code") or d.get("region"), d.get("country"))),
    ]
    for url, parse in providers:
        try:
            data = _get_json(url)
            if data.get("error") or data.get("status") == "fail" or data.get("success") is False:
                raise ValueError(data.get("reason") or data.get("message") or "lookup failed")
            lat, lon, city, region, country = parse(data)
            name = ", ".join(p for p in (city, region) if p) or country or f"{lat:.2f},{lon:.2f}"
            _location_cache = {"lat": float(lat), "lon": float(lon), "name": name, "source": url.split("/")[2]}
            return _location_cache
        except Exception as exc:
            errors.append(f"{url.split('/')[2]}: {exc}")
    raise RuntimeError("IP geolocation failed (" + "; ".join(errors) + ")")


def _geocode(query: str) -> dict:
    # Open-Meteo's geocoder matches on the place name; use the first part,
    # then prefer a result whose admin/country matches the rest ("Denver, CO").
    parts = [p.strip() for p in query.split(",") if p.strip()]
    data = _get_json(GEOCODE_URL, {"name": parts[0], "count": 10, "language": "en", "format": "json"})
    results = data.get("results") or []
    if not results:
        raise ValueError(f"could not geocode location {query!r}")
    best = results[0]
    if len(parts) > 1:
        hint = parts[1].lower()
        for r in results:
            fields = [str(r.get(k, "")).lower() for k in ("admin1", "country", "country_code", "admin1_code")]
            if any(hint == f or (len(hint) > 2 and hint in f) for f in fields):
                best = r
                break
    name = ", ".join(p for p in (best.get("name"), best.get("admin1")) if p)
    return {"lat": best["latitude"], "lon": best["longitude"], "name": name, "source": "config"}


def resolve_location(setting) -> dict:
    if isinstance(setting, dict) and "lat" in setting and "lon" in setting:
        return {"lat": float(setting["lat"]), "lon": float(setting["lon"]),
                "name": setting.get("name") or f"{setting['lat']},{setting['lon']}", "source": "config"}
    setting = (setting or "").strip() if isinstance(setting, str) else ""
    if not setting:
        return _ip_location()
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", setting)
    if m:
        return {"lat": float(m[1]), "lon": float(m[2]), "name": setting, "source": "config"}
    return _geocode(setting)


# Weather --------------------------------------------------------------------
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,wind_gusts_10m_max"
CURRENT = "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m"


def _unit_params(units: str) -> tuple[dict, dict]:
    if units == "metric":
        return ({"temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm"},
                {"t": "°C", "w": "km/h", "p": "mm"})
    return ({"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch"},
            {"t": "°F", "w": "mph", "p": "in"})


def _compass(deg) -> str:
    if deg is None:
        return ""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg % 360) / 45 + 0.5) % 8]


def _daily_ok(data: dict) -> bool:
    d = data.get("daily") or {}
    return bool(d.get("time")) and d.get("temperature_2m_max", [None])[0] is not None


_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_SECONDS = 15 * 60


def fetch_weather(date: dt.date, loc: dict, units: str = "imperial", contact: str = "") -> dict:
    """Cached wrapper (15 min per date/place/units) to spare the free API quotas.

    Tries Open-Meteo first; on failure falls back to MET Norway when the date
    is within MET's forecast horizon (today .. +9 days).
    """
    import time
    key = (date, round(loc["lat"], 3), round(loc["lon"], 3), units)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        result = _fetch_weather(date, loc, units)
        result.setdefault("provider", "Open-Meteo")
    except Exception as om_exc:
        days = (date - dt.date.today()).days
        if not (0 <= days <= MET_MAX_DAYS):
            raise
        try:
            result = fetch_met_norway(date, loc, units, contact)
        except Exception as met_exc:
            raise RuntimeError(f"{om_exc}  |  MET Norway backup also failed: {met_exc}") from met_exc
        result["fallback_reason"] = str(om_exc)
    _cache[key] = (time.time(), result)
    return result


# ---- MET Norway backup ----------------------------------------------------
MET_SYMBOLS = {  # symbol_code prefix -> (description, emoji, WMO-ish code)
    "clearsky": ("Clear sky", "☀️"), "fair": ("Mainly clear", "🌤️"),
    "partlycloudy": ("Partly cloudy", "⛅"), "cloudy": ("Overcast", "☁️"), "fog": ("Fog", "🌫️"),
    "lightrainshowers": ("Light showers", "🌦️"), "rainshowers": ("Showers", "🌦️"),
    "heavyrainshowers": ("Heavy showers", "🌧️"), "lightrain": ("Light rain", "🌦️"),
    "rain": ("Rain", "🌧️"), "heavyrain": ("Heavy rain", "🌧️"),
    "lightsleet": ("Light sleet", "🌨️"), "sleet": ("Sleet", "🌨️"), "heavysleet": ("Heavy sleet", "🌨️"),
    "lightsleetshowers": ("Light sleet showers", "🌨️"), "sleetshowers": ("Sleet showers", "🌨️"),
    "heavysleetshowers": ("Heavy sleet showers", "🌨️"),
    "lightsnow": ("Light snow", "🌨️"), "snow": ("Snow", "🌨️"), "heavysnow": ("Heavy snow", "❄️"),
    "lightsnowshowers": ("Light snow showers", "🌨️"), "snowshowers": ("Snow showers", "🌨️"),
    "heavysnowshowers": ("Heavy snow showers", "❄️"),
}


def met_symbol(code: str | None) -> tuple[str, str]:
    if not code:
        return ("Unknown", "🌡️")
    base = code.split("_")[0]
    if "thunder" in base:
        return ("Thunderstorm", "⛈️")
    return MET_SYMBOLS.get(base, (base.capitalize(), "🌡️"))


def _approx_utc_offset(loc: dict) -> dt.timedelta:
    """MET gives UTC times and no time zone; use the location's zone if we know
    it (loc["tz"]), else the solar approximation round(lon / 15) hours."""
    tz = loc.get("tz")
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return dt.datetime.now(ZoneInfo(tz)).utcoffset() or dt.timedelta()
        except Exception:
            pass
    return dt.timedelta(hours=round(float(loc["lon"]) / 15))


def parse_met(payload: dict, date: dt.date, loc: dict, units: str = "imperial",
              now: dt.datetime | None = None) -> dict:
    """Turn a Locationforecast 'compact' response into the Open-Meteo-shaped
    dict format_weather understands (daily + optional current)."""
    offset = _approx_utc_offset(loc)
    series = payload.get("properties", {}).get("timeseries", [])
    rows = []
    for item in series:
        t = dt.datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        local = (t + offset).replace(tzinfo=None)
        rows.append((t, local, item.get("data", {})))
    day = [r for r in rows if r[1].date() == date]
    if not day:
        raise ValueError(f"MET Norway has no forecast for {date}")
    temps = [r[2]["instant"]["details"].get("air_temperature") for r in day]
    temps = [x for x in temps if x is not None]
    winds = [r[2]["instant"]["details"].get("wind_speed") for r in day]
    winds = [x for x in winds if x is not None]
    gusts = [r[2]["instant"]["details"].get("wind_speed_of_gust") for r in day]
    gusts = [x for x in gusts if x is not None]
    # Precipitation: hourly amounts where available, else 6-hour blocks
    # (without double counting hours already covered).
    precip, covered_until = 0.0, None
    symbols = []
    for t, _local, data in day:
        if covered_until and t < covered_until:
            continue
        if "next_1_hours" in data:
            precip += data["next_1_hours"].get("details", {}).get("precipitation_amount", 0.0) or 0.0
            symbols.append(data["next_1_hours"].get("summary", {}).get("symbol_code"))
            covered_until = t + dt.timedelta(hours=1)
        elif "next_6_hours" in data:
            precip += data["next_6_hours"].get("details", {}).get("precipitation_amount", 0.0) or 0.0
            symbols.append(data["next_6_hours"].get("summary", {}).get("symbol_code"))
            covered_until = t + dt.timedelta(hours=6)
    # Most "severe"-looking daytime symbol: prefer the one around local noon.
    noon = min(day, key=lambda r: abs((r[1].hour + r[1].minute / 60) - 13))
    nd = noon[2]
    day_symbol = ((nd.get("next_6_hours") or nd.get("next_1_hours") or nd.get("next_12_hours") or {})
                  .get("summary", {}).get("symbol_code")) or (symbols[0] if symbols else None)

    imperial = units != "metric"
    tconv = (lambda c: c * 9 / 5 + 32) if imperial else (lambda c: c)
    wconv = (lambda ms: ms * 2.236936) if imperial else (lambda ms: ms * 3.6)
    pconv = (lambda mm: mm / 25.4) if imperial else (lambda mm: mm)
    _, labels = _unit_params(units)
    daily = {
        "time": [date.isoformat()],
        "met_symbol": [day_symbol],
        "temperature_2m_max": [tconv(max(temps))] if temps else [None],
        "temperature_2m_min": [tconv(min(temps))] if temps else [None],
        "precipitation_sum": [pconv(precip)],
        "wind_speed_10m_max": [wconv(max(winds))] if winds else [None],
        "wind_gusts_10m_max": [wconv(max(gusts))] if gusts else [None],
    }
    data = {"daily": daily}
    now = now or dt.datetime.now(dt.timezone.utc)
    if date == (now + offset).date():
        cur_row = min(rows, key=lambda r: abs((r[0] - now).total_seconds()))
        det = cur_row[2]["instant"]["details"]
        sym = (cur_row[2].get("next_1_hours") or cur_row[2].get("next_6_hours") or {}).get("summary", {}).get("symbol_code")
        data["current"] = {
            "met_symbol": sym,
            "temperature_2m": tconv(det.get("air_temperature", 0.0)),
            "apparent_temperature": None,
            "relative_humidity_2m": det.get("relative_humidity"),
            "wind_speed_10m": wconv(det.get("wind_speed", 0.0)),
            "wind_direction_10m": det.get("wind_from_direction"),
        }
    if daily["temperature_2m_max"][0] is None:
        raise ValueError(f"MET Norway returned no temperatures for {date}")
    return {"data": data, "labels": labels, "provider": "MET Norway"}


def met_user_agent(contact: str = "") -> str:
    contact = (contact or "").strip()
    return f"{MET_USER_AGENT[:-1]}; {contact})" if contact else MET_USER_AGENT


def fetch_met_norway(date: dt.date, loc: dict, units: str = "imperial", contact: str = "") -> dict:
    # MET asks for at most 4 decimals in coordinates (better cache hit rate).
    params = {"lat": f"{float(loc['lat']):.4f}", "lon": f"{float(loc['lon']):.4f}"}
    payload = _get_json(MET_URL, params, user_agent=met_user_agent(contact))
    return parse_met(payload, date, loc, units)


def _fetch_weather(date: dt.date, loc: dict, units: str = "imperial") -> dict:
    unit_params, labels = _unit_params(units)
    base = {"latitude": loc["lat"], "longitude": loc["lon"], "timezone": "auto",
            "daily": DAILY, "start_date": date.isoformat(), "end_date": date.isoformat(), **unit_params}
    today = dt.date.today()
    if date == today:
        chain = [(FORECAST_URL, {**base, "current": CURRENT}), (HIST_FORECAST_URL, {**base, "current": CURRENT})]
    elif date < today:
        # Archive (ERA5) lags ~5 days; forecast APIs cover the recent past.
        chain = [(ARCHIVE_URL, base), (FORECAST_URL, base), (HIST_FORECAST_URL, base)]
    elif (date - today).days <= 15:
        chain = [(FORECAST_URL, base)]
    else:
        raise ValueError("no forecast available more than 16 days ahead")
    data, errors = None, []
    for url, params in chain:
        try:
            data = _get_json(url, params)
            if _daily_ok(data):
                break
            errors.append(f"{url.split('/')[2]}: no data")
        except Exception as exc:  # HTTP 429 (quota), timeouts, ...
            errors.append(f"{url.split('/')[2]}: {exc}")
        data = None
    if data is None:
        if errors and all("429" in e for e in errors):
            raise RuntimeError("Open-Meteo's free daily request limit is used up for this network/IP; "
                               "try Refresh Plugin Blocks later (usually resets within a day).")
        raise RuntimeError(f"no weather data for {date} (" + "; ".join(errors) + ")")
    if not _daily_ok(data):
        raise ValueError(f"no weather data available for {date}")
    return {"data": data, "labels": labels}


def format_weather(date: dt.date, loc: dict, result: dict) -> str:
    data, u = result["data"], result["labels"]
    d = {k: v[0] for k, v in data["daily"].items()}
    if "met_symbol" in d:
        desc, emoji = met_symbol(d.get("met_symbol"))
    else:
        desc, emoji = WMO.get(d.get("weather_code"), ("Unknown", "🌡️"))
    provider = result.get("provider", "Open-Meteo")
    lines = []
    cur = data.get("current")
    if cur:
        if "met_symbol" in cur:
            cdesc, cemoji = met_symbol(cur.get("met_symbol")) if cur.get("met_symbol") else (desc, emoji)
        else:
            cdesc, cemoji = WMO.get(cur.get("weather_code"), (desc, emoji))
        feels = (f" (feels like {cur['apparent_temperature']:.0f}{u['t']})"
                 if cur.get("apparent_temperature") is not None else "")
        hum = (f", humidity {cur['relative_humidity_2m']:.0f}%"
               if cur.get("relative_humidity_2m") is not None else "")
        lines.append(
            f"{cemoji} **{cdesc}**, {cur['temperature_2m']:.0f}{u['t']}{feels}{hum}, "
            f"wind {cur['wind_speed_10m']:.0f} {u['w']} {_compass(cur.get('wind_direction_10m'))}".rstrip()
        )
        lines.append("")
    else:
        lines.append(f"{emoji} **{desc}**")
        lines.append("")
    lines.append(f"- High / Low: {d['temperature_2m_max']:.0f}{u['t']} / {d['temperature_2m_min']:.0f}{u['t']}")
    precip = d.get("precipitation_sum")
    if precip is not None:
        lines.append(f"- Precipitation: {precip:.2f} {u['p']}" if u["p"] == "in" else f"- Precipitation: {precip:.1f} {u['p']}")
    if d.get("wind_speed_10m_max") is not None:
        gust = f" (gusts {d['wind_gusts_10m_max']:.0f})" if d.get("wind_gusts_10m_max") is not None else ""
        lines.append(f"- Max wind: {d['wind_speed_10m_max']:.0f} {u['w']}{gust}")
    src = "IP" if loc.get("source") not in ("config",) else "config"
    when = "now" if cur else ("forecast" if date > dt.date.today() else "historical")
    lines.append(f"- Location: {loc['name']} ({loc['lat']:.2f}, {loc['lon']:.2f}; from {src}) · {when} · {provider}")
    if result.get("fallback_reason"):
        lines.append(f"- Provider: MET Norway (backup; Open-Meteo unavailable)")
    return "\n".join(lines)


def check_location(value) -> str:
    """Settings "Look up" button: resolve the location setting to a place."""
    loc = resolve_location(value or "")
    how = "auto-detected from IP" if not (value or "").strip() else "resolved"
    return f"{how}: {loc['name']} ({loc['lat']:.4f}, {loc['lon']:.4f})"


SETTINGS = [
    {"key": "units", "label": "Units", "type": "choice", "choices": ["imperial", "metric"],
     "default": "imperial", "help": "imperial = °F, mph, inches; metric = °C, km/h, mm"},
    {"key": "location", "label": "Location", "type": "string", "default": "",
     "help": "Leave empty to auto-detect from your IP address, or enter a city "
             "(\"Denver, CO\") or \"lat,lon\" (\"39.74,-104.99\").",
     "check": check_location, "check_label": "Look up"},
    {"key": "contact", "label": "Contact for MET Norway (optional)", "type": "string", "default": "",
     "help": "MET Norway's backup weather API asks apps to identify themselves. Optionally add an "
             "email or URL; it is appended to the User-Agent only for api.met.no requests."},
]


def render(date, context) -> str:
    cfg = context.get("settings") or context.get("config", {})
    loc = resolve_location(cfg.get("location", ""))
    result = fetch_weather(date, loc, cfg.get("units", "imperial"), cfg.get("contact", ""))
    return format_weather(date, loc, result)
