"""
Core Pydantic schemas for the Bitcoin Transaction Threat Monitor.

THIS FILE IS THE CONTRACT.

Every module in this project — the synthetic data generator, the ingestion
parser, the graph builder, the scoring pipeline, the FastAPI layer, and the
React frontend — is built against the shapes defined here. If you need a new
field, change it here first and tell the team, rather than inventing a
divergent shape in your own module.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Severity buckets for a scored wallet.

    Subclasses `str` so it serializes as a plain string in JSON and satisfies
    the `severity: str` field in WalletAlert.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Transaction(BaseModel):
    """A single Bitcoin transaction, enriched with network metadata.

    One transaction may have many inputs and many outputs. `input_amounts`
    is positionally aligned with `input_addresses`, and likewise for outputs.
    """

    txid: str = Field(..., description="Transaction hash / identifier")
    timestamp: datetime = Field(..., description="When the transaction was observed")

    # Network-layer metadata (from traffic capture, not the blockchain itself)
    src_ip: str = Field(..., description="Source IP that broadcast the transaction")
    dst_ip: str = Field(..., description="Destination / peer IP")
    src_port: int = Field(..., description="Source TCP port")
    dst_port: int = Field(..., description="Destination TCP port")

    # Chain-layer metadata
    input_addresses: list[str] = Field(..., description="Wallet addresses funding the tx")
    output_addresses: list[str] = Field(..., description="Wallet addresses receiving funds")
    input_amounts: list[float] = Field(..., description="BTC per input, aligned with input_addresses")
    output_amounts: list[float] = Field(..., description="BTC per output, aligned with output_addresses")
    fee: float = Field(..., description="Miner fee in BTC")
    script_type: str = Field(..., description="e.g. P2PKH, P2SH, P2WPKH, P2TR")

    # Geo enrichment (resolved offline from a local GeoLite2 database)
    geo_country: str = Field(..., description="ISO country code of src_ip")
    asn: str = Field(..., description="Autonomous system number / name of src_ip")


class WalletAlert(BaseModel):
    """The result of scoring a single wallet for suspicious activity."""

    wallet_address: str = Field(..., description="The wallet this alert is about")
    risk_score: float = Field(..., ge=0.0, le=1.0, description="0 = benign, 1 = certainly illicit")
    confidence: float = Field(..., ge=0.0, le=1.0, description="How much we trust this score")
    severity: str = Field(..., description='One of "low", "medium", "high", "critical"')
    top_reasons: list[str] = Field(
        default_factory=list,
        description="Plain-English explanations, ordered most important first",
    )
    connected_wallets: list[str] = Field(
        default_factory=list,
        description="Wallet addresses directly linked to this one in the graph",
    )


# --------------------------------------------------------------------------
# API response shapes
#
# These wrap the two core models above for transport. They live here rather
# than in main.py because the frontend builds against them too, and this file
# is where the team looks for the shapes it can rely on.
# --------------------------------------------------------------------------


class GraphPayload(BaseModel):
    """A graph serialized for react-force-graph.

    `truncated` matters: the full demo graph has ~20,000 links, which will
    lock up a browser. When the server trims the payload it says so, rather
    than quietly returning a different graph than the one that was asked for.
    """

    nodes: list[dict] = Field(default_factory=list)
    links: list[dict] = Field(default_factory=list)
    total_nodes: int = Field(0, description="Nodes in the full graph, before filtering")
    total_links: int = Field(0, description="Links in the full graph, before filtering")
    truncated: bool = Field(False, description="True if nodes or links were dropped")


class WalletDetail(WalletAlert):
    """A WalletAlert plus the neighbourhood around it, for the detail panel."""

    subgraph: GraphPayload = Field(default_factory=GraphPayload)
    hops: int = Field(2, description="How many hops of the graph the subgraph spans")


class IngestSummary(BaseModel):
    """What POST /ingest reports back."""

    status: str = "processed"
    filename: str | None = None
    transactions: int = 0
    wallets_scored: int = 0
    high_risk_count: int = Field(
        0, description='Wallets at severity "high" or "critical"'
    )
    duration_seconds: float = 0.0


class HealthStatus(BaseModel):
    """Liveness plus enough state to tell why the dashboard might be empty."""

    status: str = "ok"
    graph_loaded: bool = False
    transactions: int = 0
    wallets_scored: int = 0
    models_loaded: bool = False
    model_feature_space: str | None = None
    geoip_available: bool = False
