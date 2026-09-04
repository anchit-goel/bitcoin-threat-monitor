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


class ActorCard(BaseModel):
    """One resolved entity (or a single unresolved high-risk wallet), sized
    for the investigation board's grid of cards.

    Scores here are 0-100 integers, not the 0-1 floats WalletAlert uses -
    this model serves a summary card, where a whole number reads faster than
    a decimal, and the frontend needn't rescale.
    """

    actor_id: str
    member_wallet_ids: list[str]
    aggregate_risk_score: int = Field(..., ge=0, le=100)
    confidence: int = Field(..., ge=0, le=100)
    short_summary: str
    connected_actor_ids: list[str] = Field(default_factory=list)


class ActorConnection(BaseModel):
    target_actor_id: str
    link_type: str
    amount_btc: float


class ActorDetail(ActorCard):
    """An ActorCard plus why it was formed and what it connects to."""

    top_reasons: list[str] = Field(default_factory=list)
    actor_connections: list[ActorConnection] = Field(default_factory=list)


class ContributingFeature(BaseModel):
    name: str
    raw: float
    max: float
    unit: str


class ConnectedWalletInfo(BaseModel):
    address: str
    risk_score: int = Field(..., ge=0, le=100)
    severity: str
    relation: str


class TrailHop(BaseModel):
    """One hop of a wallet's real onward money trail - not illustrative,
    walked from the actual graph."""

    step: int
    from_wallet: str
    to_wallet: str
    to_label: str
    amount_btc: float
    amount_usd: float
    timestamp: str
    tx_hash: str
    to_score: int = Field(..., ge=0, le=100)
    to_severity: str


class WalletDossier(BaseModel):
    """The full investigative profile of one wallet - real data throughout,
    not the WalletAlert summary. Separate from WalletAlert/WalletDetail
    rather than extending them, since this is a different consumer (a rich
    dossier UI) with a different, larger shape."""

    wallet_id: str
    address: str
    address_full: str
    risk_score: int = Field(..., ge=0, le=100)
    confidence: int = Field(..., ge=0, le=100)
    severity: str
    first_seen: str
    last_active: str
    tx_count: int
    total_volume_btc: float
    velocity_data: list[int]
    ai_narrative: str
    contributing_features: list[ContributingFeature]
    connected_wallets: list[ConnectedWalletInfo]
    trail: list[TrailHop]


class GeoFlowWallet(BaseModel):
    """One real wallet-to-wallet transfer contributing to a GeoFlow's total.

    Lets the frontend drill from an aggregated country-pair line down to the
    actual wallets that moved value on it, and open their real dossiers -
    the same wallets scored everywhere else in the app, not invented ones.
    """

    from_wallet: str
    to_wallet: str
    amount_btc: float
    risk_score: int = Field(..., ge=0, le=100)


class GeoFlow(BaseModel):
    """Aggregated value moved between two countries.

    Both ends are the INFERRED country of a wallet - the majority
    geo_country among the transactions it appears in - not a literal
    src/dst pair, since a single transaction only records one geo_country
    (the observing capture point), not distinct sender/receiver locations.
    """

    from_country: str
    to_country: str
    amount: float
    risk_score: int = Field(..., ge=0, le=100)
    sample_wallets: list[GeoFlowWallet] = Field(default_factory=list)


class HealthStatus(BaseModel):
    """Liveness plus enough state to tell why the dashboard might be empty."""

    status: str = "ok"
    graph_loaded: bool = False
    transactions: int = 0
    wallets_scored: int = 0
    models_loaded: bool = False
    model_feature_space: str | None = None
    geoip_available: bool = False
