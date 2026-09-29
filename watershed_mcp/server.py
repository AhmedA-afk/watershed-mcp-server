"""Watershed MCP Server (owockibot bounty #477).

Exposes live watershed data as MCP tools:
- search_rivers: find USGS stream gauges by river name
- sites_nearby: find active stream gauges near a lat/lng
- get_gauge_readings: live USGS water-quality readings for a gauge,
  emitted as ecological-sensor readings conforming to the #450 JSON schema
  (xtratrezzy/ecological-sensor-schema, schema_version 1.0.0)
- epa_compliance_ranking: EPA ECHO Clean Water Act compliance aggregates
  for a region, with a 0-100 score and letter grade
- compare_watersheds: rank multiple locations by EPA compliance score

Transport: stdio (default). No API keys needed — all sources are public.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import sources as _sources
from .sources import (
    WQ_PARAM_MAP,
    compliance_score,
    fetch_echo_compliance,
    fetch_iv_readings,
    search_usgs_sites,
)

SCHEMA_VERSION = "1.0.0"
SCHEMA_REF = (
    "https://github.com/xtratrezzy/ecological-sensor-schema/"
    "blob/main/schema/ecological-sensor-reading.schema.json"
)
SOURCE_LABEL = "USGS NWIS Water Services via owockibot bounty #477 watershed MCP server"

server = MCPServer("watershed-mcp")

# Schema-valid value ranges (from the #450 schema conditionals); readings
# outside these ranges are dropped so output always validates.
RANGES = {
    "ph": (0, 14),
    "dissolved_oxygen": (0, 20),
    "turbidity": (0, 4000),
    "temperature": (-5, 45),
}


def _latest_good_value(values: list[dict]) -> dict | None:
    for v in reversed(values):
        val = v.get("value")
        if val in (None, "", "-999999"):
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        return {"value": num, "dateTime": v.get("dateTime"), "qualifiers": v.get("qualifiers", [])}
    return None


def _to_schema_reading(site_info: dict, param_code: str, latest: dict) -> dict | None:
    if param_code not in WQ_PARAM_MAP:
        return None
    parameter, unit, _usgs_unit = WQ_PARAM_MAP[param_code]
    value = latest["value"]
    lo, hi = RANGES[parameter]
    if not (lo <= value <= hi):
        return None
    qualifiers = latest.get("qualifiers") or []
    quality_flag = "validated" if "A" in qualifiers else "raw"
    return {
        "schema_version": SCHEMA_VERSION,
        "sensor_id": f"USGS:{site_info['site_no']}:{param_code}",
        "sensor_type": "water_quality",
        "parameter": parameter,
        "value": value,
        "unit": unit,
        "timestamp": latest["dateTime"],
        "location": {"lat": site_info["lat"], "lng": site_info["lng"]},
        "source": SOURCE_LABEL,
        "quality_flag": quality_flag,
        "notes": f"USGS station {site_info['station_name']} ({site_info['site_no']}); qualifiers={','.join(qualifiers) or 'none'}",
    }


@server.tool()
def search_rivers(
    name: str, state: str | None = None, lat: float | None = None, lng: float | None = None, limit: int = 10
) -> dict[str, Any]:
    """Search USGS for active stream gauges whose station name contains `name`
    (e.g. 'Colorado'). Needs a geographic scope: a 2-letter `state` code
    (e.g. 'co') or a `lat`/`lng` point (1-degree box). Returns site numbers,
    names, coordinates and HUC codes."""
    sites = search_usgs_sites(name, state=state, lat=lat, lng=lng, limit=max(1, min(limit, 50)))
    return {"count": len(sites), "sites": sites}


@server.tool()
def sites_nearby(lat: float, lng: float, radius_miles: float = 25.0, limit: int = 10) -> dict[str, Any]:
    """Find active USGS stream gauges within a radius of a lat/lng point."""
    sites = _sources.sites_nearby(lat, lng, radius_miles=radius_miles, limit=max(1, min(limit, 50)))
    return {"lat": lat, "lng": lng, "radius_miles": radius_miles, "count": len(sites), "sites": sites}


@server.tool()
def get_gauge_readings(site_no: str) -> dict[str, Any]:
    """Fetch the latest live water-quality readings for a USGS gauge
    (8-digit site number). Returns ecological-sensor readings conforming to
    the #450 JSON schema (draft 2020-12, schema_version 1.0.0):
    temperature (celsius), pH, dissolved oxygen (mg/L), turbidity (NTU)."""
    site_info, series = fetch_iv_readings(site_no)
    readings = []
    raw: dict[str, Any] = {}
    for s in series:
        code = s["param_code"]
        latest = _latest_good_value(s["values"])
        if not latest:
            continue
        raw[code] = {
            "value": latest["value"],
            "usgs_unit": s["usgs_unit"],
            "timestamp": latest["dateTime"],
        }
        reading = _to_schema_reading(site_info, code, latest)
        if reading:
            readings.append(reading)
    return {
        "site": site_info,
        "schema_ref": SCHEMA_REF,
        "readings": readings,
        "unmapped_raw": {k: v for k, v in raw.items() if k not in WQ_PARAM_MAP},
    }


@server.tool()
def epa_compliance_ranking(lat: float, lng: float, radius_miles: float = 25.0) -> dict[str, Any]:
    """EPA ECHO Clean Water Act compliance ranking for the region around a
    lat/lng: facility count, violation counts, inspections, total penalties,
    plus a 0-100 compliance score and letter grade (higher = cleaner)."""
    agg = fetch_echo_compliance(lat, lng, radius_miles=radius_miles)
    return {
        "lat": lat,
        "lng": lng,
        "radius_miles": radius_miles,
        "source": "EPA ECHO CWA REST services (https://echodata.epa.gov/echo)",
        **compliance_score(agg),
    }


@server.tool()
def compare_watersheds(locations: list[dict[str, Any]], radius_miles: float = 25.0) -> dict[str, Any]:
    """Rank multiple locations by EPA compliance score. Each location is
    {'name': str, 'lat': float, 'lng': float}. Returns best-to-worst ranking."""
    ranked = []
    for loc in locations:
        agg = fetch_echo_compliance(float(loc["lat"]), float(loc["lng"]), radius_miles=radius_miles)
        entry = {"name": loc.get("name", f"{loc['lat']},{loc['lng']}"), "lat": loc["lat"], "lng": loc["lng"]}
        entry.update(compliance_score(agg))
        ranked.append(entry)
    ranked.sort(key=lambda e: (e["compliance_score"] is None, -(e["compliance_score"] or 0)))
    return {"radius_miles": radius_miles, "ranking": ranked}


def main() -> None:
    server.run()  # stdio transport


if __name__ == "__main__":
    main()
