"""FastAPI entrypoint for the Bitcoin Transaction Threat Monitor.

Run locally from inside /backend with:

    uvicorn app.main:app --reload --port 8000

Two things must happen before this API is useful:

  1. The wallet-space models must exist. Run
     `python -m app.services.wallet_model` first - the API loads them once at
     startup rather than per request, and starts in a degraded state (serving
     /health, refusing /ingest with an explanation) if they are missing.
  2. A dataset must be POSTed to /ingest. The graph lives in memory and starts
     empty, so /alerts and /graph return nothing until then. /health says which
     of these is the reason the dashboard is blank.

Nothing here makes an outbound network call. Geo enrichment reads a local
MaxMind database; see app/services/geoip.py.
"""

from __future__ import annotations

import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import networkx as nx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.models import (
    GraphPayload,
    HealthStatus,
    IngestSummary,
    Severity,
    WalletAlert,
    WalletDetail,
)
from app.services import geoip, scoring, wallet_model
from app.services.graph_builder import graph_to_json, get_subgraph
from app.services.ingestion import SUPPORTED_EXTENSIONS, load_transactions

# Uploads are read to disk before parsing, so a runaway file would fill the
# volume rather than merely failing a request.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024

# Explanations are built for the riskiest wallets at ingest time. The rest are
# computed on demand when a wallet is actually opened - nobody scrolls to the
# nine-hundredth row of a list sorted by risk.
EXPLAIN_TOP_N = 150

# The full demo graph has ~20,000 links, which will lock up a browser. /graph
# trims to this many nodes unless asked otherwise, and says when it has.
DEFAULT_GRAPH_NODE_LIMIT = 600

SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
HIGH_RISK = {Severity.HIGH.value, Severity.CRITICAL.value}


class AppState:
    """Everything the API holds between requests.

    A single instance, deliberately: this is a single-analyst tool with one
    dataset loaded at a time, and a database would be ceremony around a dict.
    """

    def __init__(self) -> None:
        self.graph: nx.DiGraph | None = None
        self.alerts: list[dict[str, Any]] = []
        self.alerts_by_wallet: dict[str, dict[str, Any]] = {}
        self.transaction_count = 0
        self.source_filename: str | None = None

        self.rf_model = None
        self.iso_model = None
        self.manifest: dict[str, Any] = {}
        self.models_loaded = False
        self.model_error: str | None = None

    def reset_graph(self) -> None:
        self.graph = None
        self.alerts = []
        self.alerts_by_wallet = {}
        self.transaction_count = 0
        self.source_filename = None


state = AppState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Load the models once, at startup.

    A missing model is not fatal. The API comes up and says so through /health,
    which is far easier to diagnose during a demo than a process that refuses
    to start.
    """
    try:
        state.rf_model, state.iso_model, state.manifest = wallet_model.load()
        state.models_loaded = True
    except FileNotFoundError as exc:
        state.model_error = str(exc)
        state.models_loaded = False

    yield

    geoip.close()


app = FastAPI(
    title="Bitcoin Transaction Threat Monitor",
    description=(
        "Ingests Bitcoin transaction metadata, builds a wallet/IP graph, scores "
        "wallets for suspicious activity, and serves results to the dashboard."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# The Vite dev server runs on 5173. 127.0.0.1 is listed alongside localhost
# because browsers treat them as distinct origins.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _require_models() -> None:
    if not state.models_loaded:
        raise HTTPException(
            status_code=503,
            detail=(
                state.model_error
                or "Models are not loaded. Run `python -m app.services.wallet_model`."
            ),
        )


def _require_graph() -> nx.DiGraph:
    if state.graph is None:
        raise HTTPException(
            status_code=409,
            detail="No dataset loaded. POST a transaction file to /ingest first.",
        )
    return state.graph


def _save_upload(upload: UploadFile) -> Path:
    """Stream an upload to a temporary file, keeping only its extension.

    The client-supplied filename is never used as a path. Only its suffix is
    taken - and only after checking it against the formats the parser accepts -
    so a name like "../../etc/passwd" cannot influence where anything lands.
    """
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '{suffix or upload.filename}'. "
                f"Expected one of: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            ),
        )

    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    written = 0
    try:
        with handle:
            while chunk := upload.file.read(UPLOAD_CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File exceeds the {MAX_UPLOAD_BYTES // (1024*1024)} MB limit."
                        ),
                    )
                handle.write(chunk)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise

    if written == 0:
        Path(handle.name).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    return Path(handle.name)


def _annotate_graph(graph: nx.DiGraph, alerts: list[dict[str, Any]]) -> None:
    """Write scores back onto the graph nodes.

    graph_to_json reads risk_score off each node, so the graph view can colour
    wallets without a second round trip per node.
    """
    for alert in alerts:
        node = graph.nodes.get(alert["wallet_address"])
        if node is not None:
            node["risk_score"] = alert["risk_score"]
            node["severity"] = alert["severity"]
            node["confidence"] = alert["confidence"]


def _explain(wallet: str) -> dict[str, Any]:
    """Fetch an alert, computing its explanation if it has not been built yet."""
    alert = state.alerts_by_wallet.get(wallet)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Unknown wallet: {wallet}")

    if not alert["top_reasons"] and state.graph is not None:
        scoring.attach_explanations(
            state.graph, [alert], state.rf_model, state.manifest
        )
    return alert


def _explain_all(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure a whole page of alerts carries its reasons, in one SHAP pass.

    Calling _explain per alert would issue one SHAP call each; a page of a
    thousand alerts took minutes that way.
    """
    if state.graph is not None:
        scoring.attach_explanations(
            state.graph, alerts, state.rf_model, state.manifest
        )
    return alerts


def _trim(payload: dict[str, Any], limit: int | None) -> GraphPayload:
    """Cap a graph payload at `limit` nodes, keeping the riskiest."""
    nodes, links = payload["nodes"], payload["links"]
    total_nodes, total_links = len(nodes), len(links)

    if limit is not None and total_nodes > limit:
        nodes = sorted(
            nodes, key=lambda n: (n.get("risk_score") or 0.0), reverse=True
        )[:limit]
        keep = {n["id"] for n in nodes}
        links = [l for l in links if l["source"] in keep and l["target"] in keep]

    return GraphPayload(
        nodes=nodes,
        links=links,
        total_nodes=total_nodes,
        total_links=total_links,
        truncated=len(nodes) < total_nodes or len(links) < total_links,
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@app.get("/health", response_model=HealthStatus, tags=["system"])
def health() -> HealthStatus:
    """Liveness, plus enough state to explain an empty dashboard."""
    return HealthStatus(
        status="ok",
        graph_loaded=state.graph is not None,
        transactions=state.transaction_count,
        wallets_scored=len(state.alerts),
        models_loaded=state.models_loaded,
        model_feature_space=state.manifest.get("feature_space"),
        geoip_available=geoip.is_available(),
    )


@app.post("/ingest", response_model=IngestSummary, tags=["pipeline"])
def ingest(file: UploadFile = File(...)) -> IngestSummary:
    """Load a transaction file, build the graph, and score every wallet.

    Accepts JSON, CSV or XML. Replaces whatever was loaded before.
    """
    _require_models()
    started = time.perf_counter()
    path = _save_upload(file)

    try:
        try:
            transactions = load_transactions(path)
        except ValueError as exc:
            # The parser's message names the offending records and fields, and
            # is the most useful thing we can hand back.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        from app.services.graph_builder import build_graph

        graph = build_graph(transactions)
        geoip.enrich_graph(graph)

        # Explanations for the top of the list only, in one batched SHAP pass;
        # the rest are built on demand when a wallet is opened.
        alerts = scoring.score_all_wallets(
            graph, state.rf_model, state.iso_model, state.manifest,
            explain_top=EXPLAIN_TOP_N,
        )

        _annotate_graph(graph, alerts)

        state.graph = graph
        state.alerts = alerts
        state.alerts_by_wallet = {a["wallet_address"]: a for a in alerts}
        state.transaction_count = len(transactions)
        state.source_filename = file.filename

        return IngestSummary(
            status="processed",
            filename=file.filename,
            transactions=len(transactions),
            wallets_scored=len(alerts),
            high_risk_count=sum(1 for a in alerts if a["severity"] in HIGH_RISK),
            duration_seconds=round(time.perf_counter() - started, 2),
        )
    finally:
        path.unlink(missing_ok=True)


@app.get("/alerts", response_model=list[WalletAlert], tags=["pipeline"])
def alerts(
    min_severity: Severity | None = Query(
        None, description='Only alerts at this severity or above'
    ),
    limit: int = Query(100, ge=1, le=5000, description="Maximum alerts to return"),
) -> list[WalletAlert]:
    """Cached wallet alerts, highest risk first."""
    _require_graph()

    selected = state.alerts
    if min_severity is not None:
        floor = SEVERITY_ORDER.index(min_severity)
        selected = [
            a for a in selected
            if SEVERITY_ORDER.index(Severity(a["severity"])) >= floor
        ]

    # Make sure everything actually returned carries its reasons.
    page = _explain_all(selected[:limit])
    return [WalletAlert.model_validate(a) for a in page]


@app.get("/wallet/{wallet_address}", response_model=WalletDetail, tags=["pipeline"])
def wallet_detail(
    wallet_address: str,
    hops: int = Query(2, ge=1, le=4, description="Neighbourhood radius"),
    limit: int = Query(
        300, ge=10, le=5000, description="Maximum nodes in the subgraph"
    ),
) -> WalletDetail:
    """One wallet's alert, with the graph neighbourhood around it."""
    graph = _require_graph()
    alert = _explain(wallet_address)

    try:
        sub = get_subgraph(graph, wallet_address, hops=hops)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return WalletDetail(
        **alert,
        hops=hops,
        subgraph=_trim(graph_to_json(sub), limit),
    )


@app.get("/graph", response_model=GraphPayload, tags=["pipeline"])
def graph_view(
    min_risk: float = Query(
        0.0, ge=0.0, le=1.0, description="Only wallets scored at or above this"
    ),
    limit: int | None = Query(
        DEFAULT_GRAPH_NODE_LIMIT,
        ge=10,
        description=(
            "Maximum nodes returned, riskiest first. The full demo graph has "
            "~20,000 links and will lock up a browser unrestricted."
        ),
    ),
    include_ips: bool = Query(True, description="Include IP nodes and their edges"),
) -> GraphPayload:
    """The scored graph, filtered for something a browser can render."""
    graph = _require_graph()
    payload = graph_to_json(graph)

    nodes = payload["nodes"]
    if not include_ips:
        nodes = [n for n in nodes if n["type"] != "ip"]
    if min_risk > 0:
        # IP nodes carry no risk score of their own; keep them only when they
        # still connect two wallets that survived the filter.
        nodes = [
            n for n in nodes
            if n["type"] == "ip" or (n.get("risk_score") or 0.0) >= min_risk
        ]

    keep = {n["id"] for n in nodes}
    links = [
        l for l in payload["links"]
        if l["source"] in keep and l["target"] in keep
    ]
    if min_risk > 0 or not include_ips:
        # Drop IP nodes left with nothing to connect after filtering.
        connected = {l["source"] for l in links} | {l["target"] for l in links}
        nodes = [
            n for n in nodes
            if n["type"] != "ip" or n["id"] in connected
        ]
        keep = {n["id"] for n in nodes}
        links = [l for l in links if l["source"] in keep and l["target"] in keep]

    trimmed = _trim({"nodes": nodes, "links": links}, limit)
    # total_* should describe the whole graph, not the filtered slice.
    trimmed.total_nodes = len(payload["nodes"])
    trimmed.total_links = len(payload["links"])
    trimmed.truncated = (
        len(trimmed.nodes) < trimmed.total_nodes
        or len(trimmed.links) < trimmed.total_links
    )
    return trimmed


@app.delete("/graph", tags=["pipeline"])
def clear_graph() -> dict[str, str]:
    """Drop the loaded dataset. Useful for resetting between demo runs."""
    state.reset_graph()
    return {"status": "cleared"}
