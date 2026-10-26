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

import hashlib
import hmac
import urllib.parse
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

    PTV's signing convention:
      - The string to sign is the raw URI path + query (no devid/signature).
      - The HMAC key is the API key (NOT the developer ID).
      - The result is hex-encoded and appended as `&signature=...`.

    Example:
      path = "/v3/departures/route_type/0/stop/1071?route_type=0&max_results=3"
      signature = HMAC-SHA1(PTV_API_KEY, path).hexdigest()
    """
    # PTV expects the raw bytes of the path, not URL-encoded
    digest = hmac.new(
        PTV_API_KEY.encode("utf-8"),
        path_with_query.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    return digest.upper()


def _signed_url(path: str, query_params: dict | None = None) -> str:
    """
    Build a fully-signed PTV API URL.

    1. Build path + query string (without devid & signature)
    2. Sign it
    3. Append devid and signature to the query
    """
    if query_params is None:
        query_params = {}

    # Build the "raw" query string (only the business params, no auth)
    raw_qs = urllib.parse.urlencode(query_params) if query_params else ""
    raw_uri = f"{path}?{raw_qs}" if raw_qs else path

    signature = _sign_request(raw_uri)

    # Now append devid and signature
    auth_params = {
        "devid": PTV_DEVELOPER_ID,
        "signature": signature,
    }
    if raw_qs:
        final_qs = f"{raw_qs}&{urllib.parse.urlencode(auth_params)}"
    else:
        final_qs = urllib.parse.urlencode(auth_params)

    return f"{PTV_BASE_URL}{path}?{final_qs}"


# ═══════════════════════════════════════════════════════════════════
# API Calls
# ═══════════════════════════════════════════════════════════════════

async def _get_json(path: str, params: dict | None = None) -> dict | None:
    """
    Perform a signed GET request to PTV API and return parsed JSON.

    Returns None on any error (network, auth, or bad response) so
    callers can handle failure gracefully with a user-facing message.
    """
    if not PTV_DEVELOPER_ID or not PTV_API_KEY:
        logger.warning("PTV API call skipped — credentials not configured.")
        return None

    url = _signed_url(path, params)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 403:
                logger.error("PTV API returned 403 — check your Developer ID and API Key.")
                return None
            else:
                logger.warning("PTV API HTTP %d for %s", resp.status_code, path)
                return None
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
    path = f"/v3/search/{urllib.parse.quote(query)}"
    data = await _get_json(path)
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
        "route_type": type_code,
        "max_results": max_results,
        "expand": ["run", "direction"],
    }

    data = await _get_json(path, params)
    if not data:
        return None

    stop_name = data.get("stop_name", "Unknown Stop")
    departures = []

    for dep in data.get("departures", [])[:max_results]:
        run = dep.get("run", {})
        route_name = run.get("route_name", run.get("route_number", "?"))
        direction = dep.get("direction", {}).get("direction_name", "Unknown")

        # Platform info
        platform = dep.get("platform_number")
        platform_str = str(platform) if platform is not None else None

        # Scheduled vs estimated departure time
        scheduled = dep.get("scheduled_departure", "")
        estimated = dep.get("estimated_departure")

        # PTV returns times like "2026-10-26T14:23:00Z" — we extract HH:MM
        sched_time = _extract_time(scheduled)
        est_time = _extract_time(estimated) if estimated else None

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

        # Check for bus replacement / flags
        flags = dep.get("flags", {})
        is_bus_replacement = "RR" in route_name.upper() or flags.get("is_bus_substitution", False)

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
    search_path = f"/v3/search/{urllib.parse.quote(route_name)}"
    search_data = await _get_json(search_path)
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

    # Fetch disruptions for this route
    disp_path = f"/v3/disruptions/route/{route_id}"
    disp_data = await _get_json(disp_path)
    if not disp_data:
        return None

    disruptions = []
    has_bus_replacement = False
    has_major_delay = False

    for d in disp_data.get("disruptions", {}).get("general", []):
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
    """Extract HH:MM from an ISO-8601 timestamp string."""
    if not iso_str:
        return None
    # Expected: "2026-10-26T14:23:00Z"
    try:
        return iso_str.split("T")[1][:5]
    except IndexError:
        return None


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