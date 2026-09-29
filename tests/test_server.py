"""Local stdio test harness for the Watershed MCP server.

Spawns `python -m watershed_mcp.server` over stdio, runs the full MCP
handshake, calls the tools, and validates gauge readings against the
vendored #450 JSON schema (draft 2020-12). Requires live internet for the
USGS/EPA checks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "ecological-sensor-reading.schema.json"


class McpClient:
    def __init__(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "watershed_mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def request(self, method: str, params: dict | None = None, timeout: int = 120) -> dict:
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            msg["params"] = params
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        assert self.proc.stdout
        line = self.proc.stdout.readline()
        return json.loads(line)

    def notify(self, method: str):
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call_tool(self, name: str, arguments: dict) -> dict:
        resp = self.request("tools/call", {"name": name, "arguments": arguments})
        if "error" in resp:
            raise RuntimeError(f"tool error: {resp['error']}")
        result = resp.get("result", {})
        if result.get("isError"):
            text = result["content"][0].get("text", "unknown tool error")
            raise RuntimeError(f"tool {name} failed: {text}")
        content = result["content"][0]
        payload = content.get("structuredContent", content.get("text"))
        return json.loads(payload) if isinstance(payload, str) else payload

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def validate_schema(readings: list[dict]) -> list[str]:
    from jsonschema import Draft202012Validator

    schema = json.load(open(SCHEMA_PATH))
    validator = Draft202012Validator(schema)
    errors = []
    for r in readings:
        for e in validator.iter_errors(r):
            errors.append(f"{r.get('sensor_id')}: {e.message}")
    return errors


def main() -> int:
    c = McpClient()
    failures: list[str] = []
    try:
        init = c.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "watershed-test", "version": "1.0"},
            },
        )
        assert "result" in init, f"init failed: {init}"
        print("OK initialize:", init["result"]["serverInfo"]["name"])
        c.notify("notifications/initialized")

        tools = c.request("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        print("OK tools/list:", names)
        for want in ("search_rivers", "sites_nearby", "get_gauge_readings",
                     "epa_compliance_ranking", "compare_watersheds"):
            assert want in names, f"missing tool {want}"

        # 1. River search (live USGS site service, state-scoped)
        res = c.call_tool("search_rivers", {"name": "Colorado", "state": "co", "limit": 3})
        assert res["count"] >= 1, "no Colorado sites found"
        print(f"OK search_rivers: {res['count']} sites, first = {res['sites'][0]['station_name']}")

        # 2. Gauge readings with live USGS data + schema validation
        res = c.call_tool("get_gauge_readings", {"site_no": "09163500"})
        site, readings = res["site"], res["readings"]
        assert site["site_no"] == "09163500", site
        assert len(readings) >= 1, "no readings returned"
        errs = validate_schema(readings)
        if errs:
            failures.append("schema validation: " + "; ".join(errs))
        print(f"OK get_gauge_readings ({site['station_name']}):")
        for r in readings:
            print(f"   {r['parameter']:>16} = {r['value']} {r['unit']} @ {r['timestamp']} [schema-valid]")
        print("   schema_ref:", res["schema_ref"])

        # 3. EPA compliance ranking (live ECHO)
        res = c.call_tool("epa_compliance_ranking", {"lat": 38.9, "lng": -77.03, "radius_miles": 20})
        assert res["facilities"] and res["facilities"] > 0, "no EPA facilities"
        print(
            f"OK epa_compliance_ranking: {res['facilities']} facilities, "
            f"score={res['compliance_score']} grade={res['grade']}, "
            f"penalties={res['total_penalties']}"
        )

        # 4. Compare watersheds
        res = c.call_tool(
            "compare_watersheds",
            {
                "locations": [
                    {"name": "Washington DC", "lat": 38.9, "lng": -77.03},
                    {"name": "Rural CO-UT", "lat": 39.0, "lng": -109.05},
                ],
                "radius_miles": 20,
            },
        )
        assert len(res["ranking"]) == 2
        order = [f"{e['name']}({e['grade']})" for e in res["ranking"]]
        print("OK compare_watersheds ranking:", " > ".join(order))

        # 5. sites_nearby
        res = c.call_tool("sites_nearby", {"lat": 44.06, "lng": -91.65, "radius_miles": 25, "limit": 5})
        print(f"OK sites_nearby: {res['count']} sites near Winona MN")
    finally:
        c.close()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL TESTS PASSED (live USGS + EPA data, #450 schema-valid)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
