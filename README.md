# Watershed MCP Server

Live watershed data as MCP tools, built for [owockibot.xyz bounty #477](https://owockibot.xyz)
($10 USDC). Exposes river queries, USGS gauge readings, and EPA compliance rankings.

## What it does

Five tools over the stdio MCP transport:

| Tool | Source | Returns |
|---|---|---|
| `search_rivers` | USGS Water Services | Active stream gauges by river name (needs a 2-letter `state` code or `lat`/`lng` scope) |
| `sites_nearby` | USGS Water Services | Active gauges within a radius of a lat/lng (radius capped at ~35 mi by USGS bbox limits) |
| `get_gauge_readings` | USGS NWIS live IV feed | Latest water-quality readings for a gauge as ecological-sensor readings conforming to the **#450 JSON schema** (draft 2020-12, `schema_version: 1.0.0`): temperature (celsius), pH, dissolved oxygen (mg/L), turbidity (NTU) |
| `epa_compliance_ranking` | EPA ECHO CWA REST | Regional compliance aggregates: facilities, violations, inspections, penalties, plus a 0-100 compliance score and letter grade |
| `compare_watersheds` | EPA ECHO CWA REST | Rank multiple locations best-to-worst by compliance score |

All data sources are public and require no API keys. The vendored #450 schema lives in
[`schema/ecological-sensor-reading.schema.json`](schema/ecological-sensor-reading.schema.json)
(original: [xtratrezzy/ecological-sensor-schema](https://github.com/xtratrezzy/ecological-sensor-schema),
submitted for bounty #450).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Stdio (for Claude Desktop / MCP clients):

```bash
python -m watershed_mcp.server
```

Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "watershed": {
      "command": "/path/to/repo/.venv/bin/python",
      "args": ["-m", "watershed_mcp.server"],
      "cwd": "/path/to/repo"
    }
  }
}
```

## Example session

```
> search_rivers("Colorado", state="co", limit=3)
{"count": 3, "sites": [{"site_no": "09163500", "station_name": "COLORADO RIVER NEAR COLORADO-UTAH STATE LINE", ...}]}

> get_gauge_readings("09163500")
{"site": {...}, "schema_ref": "https://github.com/xtratrezzy/ecological-sensor-schema/...",
 "readings": [
   {"schema_version": "1.0.0", "sensor_id": "USGS:09163500:00010", "sensor_type": "water_quality",
    "parameter": "temperature", "value": 17.8, "unit": "celsius",
    "timestamp": "2026-09-29T00:45:00.000-06:00",
    "location": {"lat": 39.0, "lng": -109.05},
    "source": "USGS NWIS Water Services via owockibot bounty #477 watershed MCP server",
    "quality_flag": "raw", ...},
   ... ]}

> epa_compliance_ranking(38.9, -77.03, radius_miles=20)
{"facilities": 3342, "violations_last_4_quarters": 175, "compliance_score": 94.8, "grade": "A", ...}
```

## Tests

```bash
python tests/test_server.py
```

The test harness spawns the server over real stdio MCP, runs `initialize` /
`tools/list` / `tools/call` against live USGS and EPA endpoints, and validates
every gauge reading against the vendored #450 schema with
`jsonschema.Draft202012Validator`.

## Submission / deploy notes (for the claimer)

1. Create a public GitHub repo (e.g. `watershed-mcp`), push this directory.
2. Zip the repo (excluding `.venv/`) and upload to Arweave (e.g. via
   [ardrive](https://ardrive.io) or `arweave` CLI) — the bounty asks for an
   Arweave-hosted build.
3. Claim bounty #477 on [owockibot.xyz](https://owockibot.xyz) with the GitHub
   repo URL + Arweave link in the submission. Note the proposer-priority window:
   only wallet `0xbc97e858bec05ba05be1e3bd63f65d3cbbecc4` may claim until
   2026-09-29T20:03:00Z; claims open to everyone after that.

## License

MIT
