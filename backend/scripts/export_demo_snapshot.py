"""Export a static JSON snapshot of a running backend for GitHub Pages.

GitHub Pages serves static files only - it cannot run FastAPI - so the
published demo reads pre-fetched JSON instead of a live API. This script
crawls every endpoint the frontend actually calls (api.ts) against a running
backend and writes the responses to frontend/public/demo-data/, in the same
shape frontend/src/api.ts expects to read them back in static-demo mode.

It does not fabricate anything: every file is a real, unmodified response
from the currently-ingested dataset. Wallet dossiers are BFS-crawled outward
from every wallet reachable through the UI (alerts, actor member wallets,
geo-flow sample wallets, then each dossier's own connected wallets and money
trail) so a demo visitor never clicks a link that 404s.

Usage (with the backend already running and a dataset ingested):

    backend/.venv/Scripts/python.exe backend/scripts/export_demo_snapshot.py

Re-run this and commit the result whenever the demo dataset or the API
contract changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
from urllib.error import HTTPError

BASE_URL = "http://localhost:8000"
OUT_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public" / "demo-data"
MAX_WALLETS = 2000  # safety cap on the BFS; the demo graph has ~1,350 wallets total


def get(path: str):
    with urlopen(f"{BASE_URL}{path}") as resp:
        return json.loads(resp.read())


def save(rel_path: Path, data) -> None:
    rel_path.parent.mkdir(parents=True, exist_ok=True)
    rel_path.write_text(json.dumps(data), encoding="utf-8")


def main() -> None:
    health = get("/health")
    if not health.get("graph_loaded"):
        print("Backend has no dataset ingested. POST a file to /ingest first.", file=sys.stderr)
        sys.exit(1)
    print(f"Snapshotting: {health}")

    wallets: set[str] = set()

    alerts = get("/alerts?limit=5000")
    save(OUT_DIR / "alerts.json", alerts)
    wallets.update(a["wallet_address"] for a in alerts)
    print(f"alerts: {len(alerts)}")

    entities = get("/entities")
    save(OUT_DIR / "entities.json", entities)
    for actor in entities:
        wallets.update(actor["member_wallet_ids"])
    print(f"entities: {len(entities)}")

    for actor in entities:
        detail = get(f"/entities/{quote(actor['actor_id'])}")
        save(OUT_DIR / "entities" / f"{actor['actor_id']}.json", detail)
    print(f"entity details: {len(entities)}")

    matrix = get("/entities/matrix")
    save(OUT_DIR / "entities-matrix.json", matrix)
    print(f"matrix: {len(matrix.get('actor_ids', []))}x{len(matrix.get('actor_ids', []))}")

    geo_flows = get("/geo-flows")
    save(OUT_DIR / "geo-flows.json", geo_flows)
    for flow in geo_flows:
        for w in flow["sample_wallets"]:
            wallets.add(w["from_wallet"])
            wallets.add(w["to_wallet"])
    print(f"geo-flows: {len(geo_flows)}")

    # BFS out from every wallet reachable so far, through each dossier's own
    # connected wallets and money-trail hops, until nothing new turns up.
    fetched: dict[str, dict] = {}
    frontier = set(wallets)
    missing = 0
    while frontier and len(fetched) < MAX_WALLETS:
        addr = frontier.pop()
        if addr in fetched:
            continue
        try:
            dossier = get(f"/wallet/{quote(addr)}/dossier")
        except HTTPError as e:
            if e.code == 404:
                missing += 1
                continue
            raise
        fetched[addr] = dossier
        save(OUT_DIR / "wallets" / f"{addr}.json", dossier)
        for cw in dossier["connected_wallets"]:
            if cw["address"] not in fetched:
                frontier.add(cw["address"])
        for hop in dossier["trail"]:
            if hop["to_wallet"] not in fetched:
                frontier.add(hop["to_wallet"])

    print(f"wallet dossiers: {len(fetched)} (missing/404: {missing})")
    print(f"Snapshot written to {OUT_DIR}")


if __name__ == "__main__":
    main()
