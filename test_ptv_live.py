"""
PTV live smoke test — run this BEFORE deploying, not after.

Exercises the bot's own PTV code path (src/ptv_api.py) against the real
timetableapi.ptv.vic.gov.au: search -> departures -> disruptions.
Prints what /ptv_next and /ptv_status would actually render, and exits
non-zero if any leg fails — catch wire-format regressions here instead
of in the Discord log.

Usage:
    python test_ptv_live.py
    # or inside the running container:
    docker exec amadeus_bot python /app/test_ptv_live.py

Credentials are read from the environment (or .env next to this file;
on Linux the file must be named .env, matching docker compose).
"""

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from ptv_api import search_stops, get_departures, get_disruptions  # noqa: E402

TIME_RE = re.compile(r"^\d{2}:\d{2}$")


async def main() -> int:
    failures = []

    # 1) stop search
    stops = await search_stops("Flinders Street")
    if not stops:
        failures.append("search_stops('Flinders Street') returned nothing (auth or wire format?)")
        print("FAIL search: no results")
        print_report(failures)
        return 1
    train_stops = [s for s in stops if s["route_type"] == 0]
    stop = (train_stops or stops)[0]
    print(f"PASS search: {stop['stop_id']} = {stop['stop_name']} (route_type {stop['route_type']})")

    # 2) departures — the leg that keeps regressing on wire format
    deps = await get_departures("train", stop["stop_id"], max_results=5, stop_name=stop["stop_name"])
    if not deps or not deps["departures"]:
        failures.append(f"get_departures returned {deps!r:.120s}")
    else:
        real = [
            d for d in deps["departures"]
            if not d["route_name"].startswith("Route ?")
            and d["direction"] != "Unknown"
            and TIME_RE.match(d["scheduled"] or "")
        ]
        if not real:
            failures.append("every departure rendered as 'Route ? / Unknown / bad time' — expansion key mapping broke")
        for d in deps["departures"]:
            flag_bits = []
            if d["is_delayed"]:
                flag_bits.append(f"+{d['delay_minutes']}min")
            if d["is_bus_replacement"]:
                flag_bits.append("BUS-REPL")
            if d["estimated"]:
                flag_bits.append(f"live {d['estimated']}")
            print(
                f"  {d['scheduled']}  {d['route_name']:24s} -> {d['direction']:24s}"
                f" plat {d['platform'] or '-':>3s}  {' '.join(flag_bits)}"
            )
        print(f"{'PASS' if not failures else 'FAIL'} departures: {len(deps['departures'])} rows, {len(real)} fully-resolved")

    # 3) disruptions lookup
    dis = await get_disruptions("Werribee")
    if dis is None:
        failures.append("get_disruptions('Werribee') returned None")
        print("FAIL disruptions")
    else:
        print(f"PASS disruptions: has_disruption={dis['has_disruption']} items={len(dis['disruptions'])}")

    print_report(failures)
    return 1 if failures else 0


def print_report(failures):
    if failures:
        print("\n--- FAILURES ---")
        for f in failures:
            print(" *", f)
        print("Fix src/ptv_api.py against the live response before deploying.")
    else:
        print("\nALL PTV LEGS OK — safe to deploy.")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
