"""Shared data-fetch helpers for the Watershed MCP server.

All data comes from public US government APIs that require no key:
- USGS Water Services (NWIS): live stream gauge readings + site search
  https://waterservices.usgs.gov
- EPA ECHO (Enforcement and Compliance History Online) REST services:
  regional Clean Water Act compliance aggregates
  https://echodata.epa.gov/echo
"""

from __future__ import annotations

import urllib.parse
import urllib.request

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
USGS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
ECHO_FACILITIES_URL = "https://echodata.epa.gov/echo/cwa_rest_services.get_facilities"

# USGS parameter code -> (schema parameter, schema unit, usgs unit label, max)
# Only codes mappable onto the #450 water_quality vocabulary are kept.
WQ_PARAM_MAP = {
    "00010": ("temperature", "celsius", "deg C"),
    "00400": ("ph", "pH", "std units"),
    "00300": ("dissolved_oxygen", "mg/L", "mg/l"),
    "63680": ("turbidity", "NTU", "FNU"),  # FNU ~ NTU for practical purposes
    "00076": ("turbidity", "NTU", "NTU"),
}

HTTP_TIMEOUT = 45


def _get_json(url: str, params: dict) -> dict:
    import json
    import time

    last: Exception | None = None
    for attempt in range(3):
        try:
            qs = urllib.parse.urlencode(params)
            req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": "watershed-mcp/1.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - these APIs are flaky
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after retries: {last}")


def _get_text(url: str, params: dict) -> str:
    import time

    last: Exception | None = None
    for attempt in range(3):
        try:
            qs = urllib.parse.urlencode(params)
            req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": "watershed-mcp/1.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - these APIs are flaky
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after retries: {last}")


def search_usgs_sites(
    name: str,
    state: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search USGS stream sites by name (RDB output, since the site service
    no longer accepts format=json).

    The site service requires a geographic major filter: pass a 2-letter
    ``state`` code (e.g. 'co') or a ``lat``/``lng`` point (searched in a
    1-degree box around it). Raises ValueError if neither is given.
    """
    params = {
        "stationNm": name,
        "format": "rdb",
        "siteOutput": "basic",
        "siteStatus": "active",
        "hasDataTypeCd": "iv",
    }
    if state:
        params["stateCd"] = state.lower()
    elif lat is not None and lng is not None:
        # USGS bbox parser rejects long decimal strings — round to 4 dp
        west, south = round(lng - 0.5, 4), round(lat - 0.5, 4)
        east, north = round(lng + 0.5, 4), round(lat + 0.5, 4)
        params["bBox"] = f"{west},{south},{east},{north}"
    else:
        raise ValueError(
            "USGS site search needs a geographic scope: pass state (2-letter code) or lat/lng."
        )
    text = _get_text(USGS_SITE_URL, params)
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    if len(lines) < 3:
        return []
    header = lines[0].split("\t")
    sites = []
    for row in lines[2:]:
        cols = row.split("\t")
        if len(cols) != len(header):
            continue
        rec = dict(zip(header, cols))
        try:
            slat, slng = float(rec["dec_lat_va"]), float(rec["dec_long_va"])
        except (ValueError, KeyError):
            continue
        sites.append(
            {
                "site_no": rec["site_no"],
                "station_name": rec["station_nm"].strip(),
                "lat": slat,
                "lng": slng,
                "huc": rec.get("huc_cd", "").strip(),
            }
        )
        if len(sites) >= limit:
            break
    return sites


def sites_nearby(lat: float, lng: float, radius_miles: float = 25.0, limit: int = 10) -> list[dict]:
    """Find active stream sites inside a bounding box around a point."""
    import math

    dlat = min(radius_miles / 69.0, 0.5)
    dlng = min(radius_miles / (69.0 * max(0.2, math.cos(math.radians(lat)))), 0.5)
    return _sites_nearby_bbox(lat, lng, dlat, dlng, limit)


def _sites_nearby_bbox(lat: float, lng: float, dlat: float, dlng: float, limit: int) -> list[dict]:
    params = {
        # USGS bbox parser rejects long decimal strings — round to 4 dp
        "bBox": f"{round(lng - dlng, 4)},{round(lat - dlat, 4)},"
        f"{round(lng + dlng, 4)},{round(lat + dlat, 4)}",
        "format": "rdb",
        "siteOutput": "basic",
        "siteStatus": "active",
        # NOTE: do not add site_tp_cd here — it 400s combined with hasDataTypeCd
        "hasDataTypeCd": "iv",
    }
    text = _get_text(USGS_SITE_URL, params)
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    if len(lines) < 3:
        return []
    header = lines[0].split("\t")
    sites = []
    for row in lines[2:]:
        cols = row.split("\t")
        if len(cols) != len(header):
            continue
        rec = dict(zip(header, cols))
        try:
            slat, slng = float(rec["dec_lat_va"]), float(rec["dec_long_va"])
        except (ValueError, KeyError):
            continue
        sites.append(
            {
                "site_no": rec["site_no"],
                "station_name": rec["station_nm"].strip(),
                "lat": slat,
                "lng": slng,
                "huc": rec.get("huc_cd", "").strip(),
            }
        )
        if len(sites) >= limit:
            break
    return sites


def fetch_iv_readings(site_no: str) -> tuple[dict, list[dict]]:
    """Fetch latest instantaneous water-quality values for a USGS site.

    Returns (site_info, series) where series items carry variable metadata
    and the raw value list from NWIS WaterML JSON.
    """
    data = _get_json(
        USGS_IV_URL,
        {
            "format": "json",
            "sites": site_no,
            "parameterCd": ",".join(WQ_PARAM_MAP.keys()),
            "siteStatus": "all",
        },
    )
    tss = data["value"]["timeSeries"]
    site_info: dict = {}
    series: list[dict] = []
    for ts in tss:
        si = ts["sourceInfo"]
        if not site_info:
            gl = si["geoLocation"]["geogLocation"]
            site_info = {
                "site_no": si["siteCode"][0]["value"],
                "station_name": si["siteName"],
                "lat": float(gl["latitude"]),
                "lng": float(gl["longitude"]),
            }
        var = ts["variable"]
        code = var["variableCode"][0]["value"]
        values = ts["values"][0]["value"]
        series.append(
            {
                "param_code": code,
                "usgs_unit": var["unit"]["unitCode"],
                "values": values,
            }
        )
    return site_info, series


def fetch_echo_compliance(lat: float, lng: float, radius_miles: float = 25.0) -> dict:
    """EPA ECHO Clean Water Act compliance aggregates for a search radius."""
    params = {
        "p_lat": lat,
        "p_long": lng,
        "p_radius": radius_miles,
        "p_federal_agency": "N",
        "output": "JSON",
    }
    # ECHO is flaky under load; retry a few times before giving up.
    import time

    last: Exception | None = None
    for attempt in range(3):
        try:
            return _get_json(ECHO_FACILITIES_URL, params).get("Results", {})
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"EPA ECHO request failed after retries: {last}")


def compliance_score(agg: dict) -> dict:
    """Turn EPA ECHO aggregate counts into a 0-100 compliance score + grade."""
    facilities = int(agg.get("QueryRows") or 0)
    viol4q = int(agg.get("VioLast4QRows") or 0)
    penalties = agg.get("TotalPenalties") or "$0"
    if facilities == 0:
        score = None
    else:
        score = round(100.0 * max(0.0, 1.0 - viol4q / facilities), 1)
    if score is None:
        grade = "N/A"
    elif score >= 95:
        grade = "A"
    elif score >= 85:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 50:
        grade = "D"
    else:
        grade = "F"
    return {
        "facilities": facilities,
        "violations_last_4_quarters": viol4q,
        "significant_violations": int(agg.get("SVRows") or 0),
        "current_violations": int(agg.get("CVRows") or 0),
        "violations_3yr": int(agg.get("V3Rows") or 0),
        "inspections": int(agg.get("INSPRows") or 0),
        "total_penalties": penalties,
        "compliance_score": score,
        "grade": grade,
    }
