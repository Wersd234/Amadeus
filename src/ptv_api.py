"""
Melbourne PTV Timetable API v3 client.

Handles:
  - HMAC-SHA1 request signing (required by PTV for every call)
  - Stop search with fuzzy matching
  - Real-time departure board for a stop
  - Disruption / bus-replacement detection

API docs: https://www.ptv.vic.gov.au/footer/data-and-reporting/datasets/ptv-timetable-api/

All functions return structured dicts; the caller (commands.py) is
responsible for formatting them into Discord embeds.
"""

import asyncio
import hashlib
import hmac
import time
import urllib.parse
from datetime import datetime
from typing import Optional

import httpx

from config import (
    logger, PTV_DEVELOPER_ID, PTV_API_KEY, PTV_BASE_URL, PTV_ROUTE_TYPES,
)


# ═══════════════════════════════════════════════════════════════════
# HMAC-SHA1 Signing
# ═══════════════════════════════════════════════════════════════════

def _sign_request(path_with_query: str) -> str:
    """
    Produce the HMAC-SHA1 hex signature required by PTV API v3.

    PTV's signing convention (the most common cause of 403s is
    getting these details wrong):
      - The string to sign is the path + query INCLUDING `devid`.
      - The scheme/host must NOT be part of the signed string.
      - The HMAC key is the API key (NOT the developer ID).
      - The digest is lowercase hex, appended last as `&signature=...`
        (the signature itself is not covered by the HMAC).

    Example:
      to_sign = "/v3/departures/route_type/0/stop/1071?max_results=3&devid=1234"
      signature = HMAC-SHA1(PTV_API_KEY, to_sign).hexdigest()
    """
    digest = hmac.new(
        PTV_API_KEY.encode("utf-8"),
        path_with_query.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    return digest


def _signed_url(path: str, query_params: dict | None = None) -> str:
    """
    Build a fully-signed PTV API URL.

    1. Build path + query string (business params only)
    2. Append `devid` BEFORE signing (it must be part of the HMAC input)
    3. Sign, then append `&signature=...`
    """
    if query_params is None:
        query_params = {}

    # Build the "raw" query string (only the business params, no auth).
    # doseq=True is required: PTV array parameters (expand, route_types)
    # use the repeated-parameter form (?expand=route&expand=run&...).
    raw_qs = urllib.parse.urlencode(query_params, doseq=True) if query_params else ""
    raw_uri = f"{path}?{raw_qs}" if raw_qs else path

    sep = "&" if "?" in raw_uri else "?"
    to_sign = f"{raw_uri}{sep}devid={PTV_DEVELOPER_ID}"

    signature = _sign_request(to_sign)

    # Wire URL must be byte-identical to the signed string + signature
    return f"{PTV_BASE_URL}{to_sign}&signature={signature}"


# ═══════════════════════════════════════════════════════════════════
# API Calls
# ═══════════════════════════════════════════════════════════════════

# Minimal TTL cache (ported from the reference client).  Autocomplete
# callbacks fire on EVERY keystroke; without a 60 s cache on /search the
# bot would trip PTV's undocumented quota within seconds.
_RESPONSE_CACHE: dict[str, tuple[float, dict]] = {}


async def _get_json(path: str, params: dict | None = None, ttl: float = 0) -> dict | None:
    """
    Perform a signed GET request to PTV API and return parsed JSON.

    When ttl > 0 the (successful) response is cached for ttl seconds.
    Returns None on any error (network, auth, or bad response) so
    callers can handle failure gracefully with a user-facing message.
    """
    if not PTV_DEVELOPER_ID or not PTV_API_KEY:
        logger.warning("PTV API call skipped — credentials not configured.")
        return None

    cache_key = f"{path}?{urllib.parse.urlencode(params or {}, doseq=True)}"
    if ttl > 0:
        hit = _RESPONSE_CACHE.get(cache_key)
        if hit is not None and time.time() < hit[0]:
            return hit[1]

    url = _signed_url(path, params)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # PTV does not document its quota — on 429 back off
            # exponentially (1s, 2s, 4s) instead of guessing (port of
            # the reference client's retry loop).
            resp = None
            for attempt in range(3):
                resp = await client.get(url)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                    await asyncio.sleep(retry_after)
                    continue
                break

            if resp is None or resp.status_code == 429:
                logger.warning("PTV API rate limited (3 retries) for %s", path)
                return None
            if resp.status_code == 403:
                logger.error("PTV API returned 403 — check your Developer ID and API Key.")
                return None
            if resp.status_code != 200:
                logger.warning("PTV API HTTP %d for %s", resp.status_code, path)
                return None

            data = resp.json()
            # PTV sometimes returns HTTP 200 with an unhealthy status
            # payload; treat it as an error so callers don't render garbage.
            status = data.get("status") or {}
            if status.get("health") not in (None, 1):
                logger.warning("PTV API unhealthy for %s: %s", path, status)
                return None
            if ttl > 0:
                _RESPONSE_CACHE[cache_key] = (time.time() + ttl, data)
            return data
    except httpx.RequestError as exc:
        logger.error("PTV API request failed: %s", exc)
        return None


async def search_stops(query: str) -> list[dict] | None:
    """
    Fuzzy search for PTV stops by name.

    Parameters
    ----------
    query : str
        Partial station/stop name, e.g. 'Flinders', 'Clayton', 'Melbourne Central'

    Returns
    -------
    list[dict] | None
        Each entry: {stop_id, stop_name, route_type, suburb}
        Returns None on API failure.
    """
    # safe='' is critical: any reserved char left unencoded would change
    # the wire URL and invalidate the HMAC signature.
    # Outlets (ticket offices) and addresses are noise for a transport
    # bot, so they are filtered out at the source.
    path = f"/v3/search/{urllib.parse.quote(query, safe='')}"
    data = await _get_json(
        path,
        {
            "include_outlets": "false",
            "include_addresses": "false",
        },
        ttl=60,  # autocomplete hammering protection (reference client TTL)
    )
    if not data:
        return None

    results = []
    for item in data.get("stops", []):
        results.append({
            "stop_id": item.get("stop_id"),
            "stop_name": item.get("stop_name"),
            "route_type": item.get("route_type"),   # 0=train, 1=tram, 2=bus
            "suburb": item.get("stop_suburb", ""),
        })
    return results


async def get_departures(
    route_type: str,
    stop_id: int,
    max_results: int = 3,
    stop_name: str | None = None,
) -> dict | None:
    """
    Get upcoming departures for a specific stop.

    Parameters
    ----------
    route_type : str
        'train', 'tram', or 'bus'
    stop_id : int
        PTV stop ID (from search_stops)
    max_results : int
        How many upcoming departures to return (default 3)

    Returns
    -------
    dict | None
        {
            stop_name: str,
            route_type: str,
            departures: [
                {
                    route_name: str,       # e.g. 'Pakenham'
                    direction: str,        # e.g. 'City (Flinders Street)'
                    platform: str | None,  # e.g. '2'
                    scheduled: str,        # HH:MM
                    estimated: str | None, # HH:MM or None if real-time unavailable
                    is_delayed: bool,
                    delay_minutes: int,
                },
                ...
            ]
        }
        Returns None on API failure.
    """
    type_code = PTV_ROUTE_TYPES.get(route_type, 0)
    path = f"/v3/departures/route_type/{type_code}/stop/{stop_id}"
    params = {
        "max_results": max_results,
        # Expanding route/run/direction inline avoids three follow-up
        # requests per departure row.  Array params are encoded as
        # repeated params via doseq=True (see _signed_url).
        "expand": ["route", "run", "direction"],
        "look_backwards": "false",
    }

    data = await _get_json(path, params)
    if not data:
        return None

    # v3 returns expanded entities as top-level maps keyed by ref/id —
    # NOT inline objects inside each departure.
    runs = data.get("runs") or {}
    directions_map = data.get("directions") or {}
    routes_map = data.get("routes") or {}

    stop_name = stop_name or data.get("stop_name") or "Unknown Stop"
    departures = []

    # NOTE: the expansion maps are keyed by STRING ids while the
    # departure/run objects carry INTEGER ids (route_id: 16 vs key "16").
    # Every map lookup must str() its key or it silently misses.
    for dep in data.get("departures", [])[:max_results]:
        run = runs.get(str(dep.get("run_ref", ""))) or {}
        route = routes_map.get(str(run.get("route_id", dep.get("route_id", "")))) or {}
        route_name = (
            route.get("route_name")
            or run.get("route_name")
            or f"Route {dep.get('route_no', '?')}"
        )
        direction = (directions_map.get(str(dep.get("direction_id", ""))) or {}).get(
            "direction_name", "Unknown"
        )

        # Platform info
        platform = dep.get("platform_number")
        platform_str = str(platform) if platform is not None else None

        # v3 field names carry the _utc suffix.  Times arrive UTC
        # ("2026-10-26T14:23:00Z"); convert to the process-local zone
        # (container TZ = Australia/Melbourne) so HH:MM is wall-clock.
        sched_time = _iso_to_local_hhmm(dep.get("scheduled_departure_utc"))
        est_time = _iso_to_local_hhmm(dep.get("estimated_departure_utc"))

        # Determine delay
        delay_minutes = 0
        is_delayed = False
        if sched_time and est_time:
            try:
                sched_mins = _time_to_minutes(sched_time)
                est_mins = _time_to_minutes(est_time)
                delay_minutes = max(0, est_mins - sched_mins)
                # Handle overnight services (e.g. 23:50 → 00:05)
                if delay_minutes > 180:
                    delay_minutes = (est_mins + 1440) - sched_mins
                is_delayed = delay_minutes >= 1
            except ValueError:
                pass

        # Check for bus replacement / flags.  v3 flags are NOT a dict:
        # a departure's "flags" is a bare string ("normal"/"express"/...)
        # and a run's "flags" is a list of strings — normalize defensively.
        raw_flags = run.get("flags") or dep.get("flags") or []
        if isinstance(raw_flags, str):
            flag_list = [raw_flags]
        elif isinstance(raw_flags, (list, tuple)):
            flag_list = [str(f) for f in raw_flags]
        else:
            flag_list = []
        is_bus_replacement = "RR" in route_name.upper() or any(
            "SUBSTITUTION" in f.upper() or "REPLACEMENT" in f.upper()
            for f in flag_list
        )

        departures.append({
            "route_name": route_name,
            "direction": direction,
            "platform": platform_str,
            "scheduled": sched_time or "?",
            "estimated": est_time,
            "is_delayed": is_delayed,
            "delay_minutes": delay_minutes,
            "is_bus_replacement": is_bus_replacement,
        })

    return {
        "stop_name": stop_name,
        "route_type": route_type,
        "departures": departures,
    }


async def get_disruptions(route_name: str) -> dict | None:
    """
    Check for active disruptions on a specific route.

    Parameters
    ----------
    route_name : str
        Route name e.g. 'Pakenham', 'Glen Waverley'

    Returns
    -------
    dict | None
        {
            route_name: str,
            has_disruption: bool,
            disruptions: [
                {title: str, description: str, disruption_type: str, status: str},
                ...
            ]
        }
    """
    # First, search for the route to get its route_id
    search_path = f"/v3/search/{urllib.parse.quote(route_name, safe='')}"
    search_data = await _get_json(search_path, ttl=60)
    if not search_data:
        return None

    # Find the first matching route (not stop)
    route_id = None
    for route in search_data.get("routes", []):
        if route_name.lower() in route.get("route_name", "").lower():
            route_id = route.get("route_id")
            route_name_full = route.get("route_name", route_name)
            break

    if not route_id:
        return {"route_name": route_name, "has_disruption": False, "disruptions": []}

    # Fetch disruptions for this route.  disruption_status=current is the
    # documented filter used by the reference client.  v3 route ids are
    # integers, but quote defensively in case a composite id shows up.
    disp_path = f"/v3/disruptions/route/{urllib.parse.quote(str(route_id), safe='')}"
    disp_data = await _get_json(disp_path, {"disruption_status": "current"}, ttl=120)
    if not disp_data:
        return None

    disruptions = []
    has_bus_replacement = False
    has_major_delay = False
    seen = set()

    # v3 buckets disruptions by severity — show all of them, not just
    # "general" (which is often empty during real disruptions).
    disp_obj = disp_data.get("disruptions") or {}
    if isinstance(disp_obj, list):
        buckets = [disp_obj]
    else:
        buckets = [disp_obj.get(k) or [] for k in ("general", "major", "minor", "significant")]

    for bucket in buckets:
      for d in bucket:
        key = (d.get("title", ""), d.get("description", ""))
        if key in seen:
            continue
        seen.add(key)
        title = d.get("title", "Disruption")
        desc = d.get("description", "")
        d_type = d.get("disruption_type", "unknown")
        status = d.get("disruption_status", "unknown")

        if "bus" in desc.lower() or "replacement" in desc.lower():
            has_bus_replacement = True
        if "major" in status.lower() or "delay" in desc.lower():
            has_major_delay = True

        disruptions.append({
            "title": title,
            "description": desc,
            "disruption_type": d_type,
            "status": status,
        })

    return {
        "route_name": route_name_full if route_name_full else route_name,
        "has_disruption": len(disruptions) > 0,
        "has_bus_replacement": has_bus_replacement,
        "has_major_delay": has_major_delay,
        "disruptions": disruptions,
    }


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _extract_time(iso_str: str) -> str | None:
    """Extract HH:MM from an ISO-8601 timestamp string (raw, no zone shift)."""
    if not iso_str:
        return None
    # Expected: "2026-10-26T14:23:00Z"
    try:
        return iso_str.split("T")[1][:5]
    except IndexError:
        return None


def _iso_to_local_hhmm(iso_str: str | None) -> str | None:
    """
    Convert a PTV UTC ISO timestamp ("2026-10-26T14:23:00Z") to local
    HH:MM using the process timezone (container TZ = Australia/Melbourne).
    The reference bot solves this with Discord <t:...:t> tags; since this
    embed shows plain HH:MM, the conversion has to happen here.
    """
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%H:%M")
    except ValueError:
        return _extract_time(iso_str)


def _time_to_minutes(hhmm: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def build_departures_summary(data: dict, lang: str) -> str:
    """
    Produce a human-readable text summary of departure data.

    This is injected into the LLM prompt so it can generate in-character
    commentary.  The caller (commands.py) separately builds the Discord
    Embed from the structured data.
    """
    if lang == "zh":
        lines = [f"车站: {data['stop_name']}", f"类型: {data['route_type']}", ""]
        for i, d in enumerate(data["departures"], 1):
            delay_str = f" ⚠️晚点{d['delay_minutes']}分钟" if d["is_delayed"] else ""
            br_str = " 🚌公交替代!" if d["is_bus_replacement"] else ""
            plat = f" {d['platform']}号站台" if d["platform"] else ""
            lines.append(
                f"#{i} {d['route_name']} → {d['direction']}{plat} "
                f"预定{d['scheduled']} 预计{d['estimated'] or '?'}{delay_str}{br_str}"
            )
        return "\n".join(lines)
    else:
        lines = [f"Stop: {data['stop_name']}", f"Type: {data['route_type']}", ""]
        for i, d in enumerate(data["departures"], 1):
            delay_str = f" ⚠️{d['delay_minutes']}min delay" if d["is_delayed"] else ""
            br_str = " 🚌Bus Replacement!" if d["is_bus_replacement"] else ""
            plat = f" Platform {d['platform']}" if d["platform"] else ""
            lines.append(
                f"#{i} {d['route_name']} → {d['direction']}{plat} "
                f"Scheduled {d['scheduled']} Est {d['estimated'] or '?'}{delay_str}{br_str}"
            )
        return "\n".join(lines)