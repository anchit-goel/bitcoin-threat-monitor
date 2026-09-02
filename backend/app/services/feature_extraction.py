"""Per-wallet graph features.

These are the inputs the wallet-space model scores, and - unlike the Elliptic
dataset's anonymised columns - every one of them has a meaning you can say out
loud. That is what lets explainability.py turn a SHAP attribution into a
sentence an analyst can act on.

Centrality measures are properties of the whole graph, not of one node, so
recomputing them inside a per-wallet call would be O(V+E) per wallet and turn
scoring 1,300 wallets into minutes of PageRank. They are computed once and
memoised onto the graph, keyed by its size so a rebuilt graph recomputes.

They are also computed on the wallet-only projection. Leaving the IP nodes in
would let a wallet's centrality rise simply because it shared a busy host,
which is a different claim from the one centrality is supposed to make.
"""

from __future__ import annotations

import statistics
from typing import Any

import networkx as nx
import pandas as pd

from app.services.graph_builder import EDGE_KIND_TRANSFER, NODE_TYPE_WALLET

# The feature vector, in a fixed order. Everything downstream - the model, the
# manifest, the SHAP explainer - depends on this order being stable.
FEATURE_NAMES = [
    "degree_centrality",
    "in_degree",
    "out_degree",
    "pagerank",
    "clustering_coefficient",
    "transaction_velocity",
    "total_sent",
    "total_received",
    "amount_variance",
    "fan_in_out_ratio",
]

_CACHE_KEY = "_feature_cache"


def wallet_nodes(graph: nx.DiGraph) -> list[str]:
    return [n for n, d in graph.nodes(data=True) if d.get("type") == NODE_TYPE_WALLET]


def _wallet_projection(graph: nx.DiGraph) -> nx.DiGraph:
    """The graph with IP nodes and broadcast edges removed."""
    keep = set(wallet_nodes(graph))
    sub = graph.__class__()
    sub.add_nodes_from((n, graph.nodes[n]) for n in keep)
    sub.add_edges_from(
        (u, v, d)
        for u, v, d in graph.edges(data=True)
        if u in keep and v in keep and d.get("kind") == EDGE_KIND_TRANSFER
    )
    return sub


def _global_metrics(graph: nx.DiGraph) -> dict[str, Any]:
    """Compute (once) the measures that depend on the whole graph."""
    signature = (graph.number_of_nodes(), graph.number_of_edges())
    cached = graph.graph.get(_CACHE_KEY)
    if cached is not None and cached["signature"] == signature:
        return cached

    projection = _wallet_projection(graph)

    if projection.number_of_nodes():
        pagerank = nx.pagerank(projection, alpha=0.85, weight="amount")
        degree_centrality = nx.degree_centrality(projection)
        # Clustering is undefined on a directed graph in the usual sense, so
        # it is measured on the undirected projection - the standard treatment.
        clustering = nx.clustering(projection.to_undirected())
    else:
        pagerank = degree_centrality = clustering = {}

    metrics = {
        "signature": signature,
        "projection": projection,
        "pagerank": pagerank,
        "degree_centrality": degree_centrality,
        "clustering": clustering,
    }
    graph.graph[_CACHE_KEY] = metrics
    return metrics


def _transfers_touching(projection: nx.DiGraph, wallet: str) -> tuple[list, list]:
    """(outgoing, incoming) individual transfers for a wallet."""
    outgoing = [
        t for _, dst in projection.out_edges(wallet)
        for t in projection[wallet][dst].get("transfers", [])
    ]
    incoming = [
        t for src, _ in projection.in_edges(wallet)
        for t in projection[src][wallet].get("transfers", [])
    ]
    return outgoing, incoming


def extract_wallet_features(graph: nx.DiGraph, wallet: str) -> dict[str, float]:
    """Compute the feature vector for one wallet.

    Raises KeyError if the wallet is not in the graph.
    """
    if wallet not in graph:
        raise KeyError(f"Wallet {wallet!r} is not present in the graph")

    metrics = _global_metrics(graph)
    projection = metrics["projection"]

    if wallet not in projection:
        # An IP node, or a wallet with no value edges at all.
        return {name: 0.0 for name in FEATURE_NAMES}

    outgoing, incoming = _transfers_touching(projection, wallet)
    amounts = [t["amount"] for t in outgoing + incoming]
    times = sorted(t["timestamp"] for t in outgoing + incoming)

    in_degree = float(projection.in_degree(wallet))
    out_degree = float(projection.out_degree(wallet))

    # Velocity in transactions per hour. A wallet seen only once has no span to
    # divide by; a floor of one minute keeps that finite and ranks a single
    # burst above a single isolated payment, rather than producing infinity.
    if len(times) >= 2:
        span_hours = max((times[-1] - times[0]).total_seconds() / 3600, 1 / 60)
    else:
        span_hours = 1 / 60
    velocity = len(times) / span_hours

    return {
        "degree_centrality": float(metrics["degree_centrality"].get(wallet, 0.0)),
        "in_degree": in_degree,
        "out_degree": out_degree,
        "pagerank": float(metrics["pagerank"].get(wallet, 0.0)),
        "clustering_coefficient": float(metrics["clustering"].get(wallet, 0.0)),
        "transaction_velocity": float(velocity),
        "total_sent": float(sum(t["amount"] for t in outgoing)),
        "total_received": float(sum(t["amount"] for t in incoming)),
        "amount_variance": float(statistics.pvariance(amounts)) if len(amounts) > 1 else 0.0,
        # +1 on the denominator: a wallet that only receives has out_degree 0,
        # and the ratio still needs to be a finite, ordered number.
        "fan_in_out_ratio": float(in_degree / (out_degree + 1.0)),
    }


def extract_all_wallets(graph: nx.DiGraph) -> pd.DataFrame:
    """Feature vectors for every wallet in the graph, indexed by address."""
    wallets = wallet_nodes(graph)
    if not wallets:
        return pd.DataFrame(columns=FEATURE_NAMES)

    _global_metrics(graph)  # warm the cache once, not per wallet
    frame = pd.DataFrame(
        [extract_wallet_features(graph, w) for w in wallets],
        index=pd.Index(wallets, name="wallet_address"),
        columns=FEATURE_NAMES,
    )
    return frame


def feature_vector(features: dict[str, float]) -> list[float]:
    """Flatten a feature dict into FEATURE_NAMES order."""
    return [float(features[name]) for name in FEATURE_NAMES]
