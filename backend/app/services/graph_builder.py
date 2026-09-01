"""Build a wallet/IP link-analysis graph from transactions.

Nodes are wallet addresses and IP addresses; edges are value flows between
them. Keeping both entity types in one graph is deliberate: it lets an
investigator pivot from a suspicious wallet to the IP that broadcast its
transactions and on to the other wallets that share that IP, which is often
the step that connects two otherwise unrelated clusters.

Run directly to build the graph from the demo dataset and export it:

    python -m app.services.graph_builder
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

from app.models import Transaction

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

NODE_TYPE_WALLET = "wallet"
NODE_TYPE_IP = "ip"

EDGE_KIND_TRANSFER = "transfer"  # wallet -> wallet, carries value
EDGE_KIND_BROADCAST = "broadcast"  # ip <-> wallet, carries no value


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def _ensure_wallet(graph: nx.DiGraph, address: str) -> None:
    if address not in graph:
        graph.add_node(
            address,
            type=NODE_TYPE_WALLET,
            total_sent=0.0,
            total_received=0.0,
            tx_count=0,
            first_seen=None,
            last_seen=None,
        )


def _ensure_ip(graph: nx.DiGraph, ip: str, geo_country: str, asn: str) -> None:
    if ip not in graph:
        graph.add_node(
            ip,
            type=NODE_TYPE_IP,
            geo_country=geo_country,
            asn=asn,
            tx_count=0,
        )


def _touch_seen(graph: nx.DiGraph, node: str, ts: datetime) -> None:
    data = graph.nodes[node]
    if data.get("first_seen") is None or ts < data["first_seen"]:
        data["first_seen"] = ts
    if data.get("last_seen") is None or ts > data["last_seen"]:
        data["last_seen"] = ts


def _add_transfer(
    graph: nx.DiGraph,
    src: str,
    dst: str,
    txid: str,
    timestamp: datetime,
    amount: float,
    fee: float,
) -> None:
    """Add or extend a wallet-to-wallet edge.

    A DiGraph holds at most one edge per ordered pair, but two wallets can
    transact repeatedly, and the domain-rule detectors need every individual
    transfer with its own timestamp to evaluate rolling time windows. So each
    edge keeps a `transfers` list of the individual movements alongside the
    aggregate `amount`/`fee`/`timestamp` attributes the schema calls for.
    """
    transfer = {"txid": txid, "timestamp": timestamp, "amount": amount, "fee": fee}

    if graph.has_edge(src, dst):
        edge = graph[src][dst]
        edge["transfers"].append(transfer)
        edge["amount"] = round(edge["amount"] + amount, 8)
        edge["fee"] = round(edge["fee"] + fee, 8)
        edge["count"] += 1
        if timestamp > edge["timestamp"]:
            # The representative txid/timestamp track the most recent movement,
            # which is what an analyst looking at a single edge cares about.
            edge["timestamp"] = timestamp
            edge["txid"] = txid
        edge["first_seen"] = min(edge["first_seen"], timestamp)
        edge["last_seen"] = max(edge["last_seen"], timestamp)
    else:
        graph.add_edge(
            src,
            dst,
            kind=EDGE_KIND_TRANSFER,
            txid=txid,
            timestamp=timestamp,
            amount=round(amount, 8),
            fee=round(fee, 8),
            count=1,
            first_seen=timestamp,
            last_seen=timestamp,
            transfers=[transfer],
        )


def build_graph(transactions: list[Transaction]) -> nx.DiGraph:
    """Build the directed wallet/IP graph.

    For each transaction, value is spread across every (input, output) pair in
    proportion to both sides:

        flow(i -> j) = (input_amount[i] / total_in) * output_amount[j]

    Bitcoin does not record which input funded which output, so a proportional
    split is the standard assumption. It has the useful property that the
    flows out of a transaction sum exactly to its total output value.

    Each transaction also links its network metadata into the same graph: an
    edge from src_ip to the largest input wallet, and one from the largest
    output wallet to dst_ip. Those edges carry no value and are tagged
    kind="broadcast" so value-based features can exclude them.
    """
    graph = nx.DiGraph()

    for tx in transactions:
        total_in = sum(tx.input_amounts)
        total_out = sum(tx.output_amounts)
        if total_in <= 0 or total_out <= 0:
            continue  # nothing meaningful to attribute

        for address in tx.input_addresses:
            _ensure_wallet(graph, address)
            _touch_seen(graph, address, tx.timestamp)
        for address in tx.output_addresses:
            _ensure_wallet(graph, address)
            _touch_seen(graph, address, tx.timestamp)

        # Fee is attributed across the same pairs, in the same proportions, so
        # that summing fee over a wallet's out-edges recovers what it paid.
        for src, in_amt in zip(tx.input_addresses, tx.input_amounts):
            in_share = in_amt / total_in
            graph.nodes[src]["total_sent"] = round(
                graph.nodes[src]["total_sent"] + in_amt, 8
            )
            graph.nodes[src]["tx_count"] += 1

            for dst, out_amt in zip(tx.output_addresses, tx.output_amounts):
                _add_transfer(
                    graph,
                    src,
                    dst,
                    txid=tx.txid,
                    timestamp=tx.timestamp,
                    amount=in_share * out_amt,
                    fee=in_share * (out_amt / total_out) * tx.fee,
                )

        for dst, out_amt in zip(tx.output_addresses, tx.output_amounts):
            graph.nodes[dst]["total_received"] = round(
                graph.nodes[dst]["total_received"] + out_amt, 8
            )

        # Link the network layer to the chain layer via the dominant wallets.
        primary_in = tx.input_addresses[tx.input_amounts.index(max(tx.input_amounts))]
        primary_out = tx.output_addresses[tx.output_amounts.index(max(tx.output_amounts))]

        _ensure_ip(graph, tx.src_ip, tx.geo_country, tx.asn)
        _ensure_ip(graph, tx.dst_ip, tx.geo_country, tx.asn)
        graph.nodes[tx.src_ip]["tx_count"] += 1
        graph.nodes[tx.dst_ip]["tx_count"] += 1

        for a, b in ((tx.src_ip, primary_in), (primary_out, tx.dst_ip)):
            if graph.has_edge(a, b):
                graph[a][b]["count"] += 1
                graph[a][b]["timestamp"] = max(graph[a][b]["timestamp"], tx.timestamp)
            else:
                graph.add_edge(
                    a,
                    b,
                    kind=EDGE_KIND_BROADCAST,
                    txid=tx.txid,
                    timestamp=tx.timestamp,
                    amount=0.0,
                    fee=0.0,
                    count=1,
                    transfers=[],
                )

    return graph


# --------------------------------------------------------------------------
# Querying
# --------------------------------------------------------------------------


def get_subgraph(graph: nx.DiGraph, wallet: str, hops: int = 2) -> nx.DiGraph:
    """Return the ego graph around `wallet`, out to `hops` edges.

    Traversal ignores edge direction. An investigator expanding a node wants
    to see who paid it as well as who it paid; following only out-edges would
    hide the funding side of the picture entirely.

    Raises:
        KeyError: the wallet is not in the graph.
    """
    if wallet not in graph:
        raise KeyError(f"Wallet {wallet!r} is not present in the graph")
    if hops < 0:
        raise ValueError(f"hops must be non-negative, got {hops}")

    return nx.ego_graph(graph, wallet, radius=hops, undirected=True)


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def graph_to_json(graph: nx.DiGraph) -> dict[str, list[dict[str, Any]]]:
    """Serialize to the {"nodes": [...], "links": [...]} shape react-force-graph wants.

    `risk_score` is emitted on every node, and is None where the wallet has
    not been scored yet. The frontend should render None as a neutral colour
    rather than treating it as zero, since "not yet assessed" and "assessed as
    safe" are different things.

    The per-edge `transfers` list is deliberately dropped here - it is an
    internal detail for the detectors, and including it would multiply the
    payload size for no visual benefit.
    """
    nodes: list[dict[str, Any]] = []
    for node_id, data in graph.nodes(data=True):
        node: dict[str, Any] = {
            "id": node_id,
            "type": data.get("type", NODE_TYPE_WALLET),
            "risk_score": data.get("risk_score"),
        }
        if node["type"] == NODE_TYPE_WALLET:
            node.update(
                total_sent=data.get("total_sent", 0.0),
                total_received=data.get("total_received", 0.0),
                tx_count=data.get("tx_count", 0),
                in_degree=graph.in_degree(node_id),
                out_degree=graph.out_degree(node_id),
                first_seen=_iso(data.get("first_seen")),
                last_seen=_iso(data.get("last_seen")),
            )
        else:
            node.update(
                geo_country=data.get("geo_country"),
                asn=data.get("asn"),
                tx_count=data.get("tx_count", 0),
            )
        # Carry through anything the scoring layer attached to the node.
        for extra in ("severity", "confidence"):
            if extra in data:
                node[extra] = data[extra]
        nodes.append(node)

    links = [
        {
            "source": src,
            "target": dst,
            "kind": data.get("kind", EDGE_KIND_TRANSFER),
            "amount": round(data.get("amount", 0.0), 8),
            "fee": round(data.get("fee", 0.0), 8),
            "count": data.get("count", 1),
            "txid": data.get("txid"),
            "timestamp": _iso(data.get("timestamp")),
        }
        for src, dst, data in graph.edges(data=True)
    ]

    return {"nodes": nodes, "links": links}


# --------------------------------------------------------------------------
# Script entrypoint
# --------------------------------------------------------------------------


def main() -> None:
    from app.services.ingestion import load_transactions

    source = DATA_DIR / "synthetic_transactions.json"
    transactions = load_transactions(source)
    graph = build_graph(transactions)

    wallets = [n for n, d in graph.nodes(data=True) if d["type"] == NODE_TYPE_WALLET]
    ips = [n for n, d in graph.nodes(data=True) if d["type"] == NODE_TYPE_IP]
    transfer_edges = [
        (u, v, d) for u, v, d in graph.edges(data=True) if d["kind"] == EDGE_KIND_TRANSFER
    ]
    total_transfers = sum(len(d["transfers"]) for _, _, d in transfer_edges)

    out_path = DATA_DIR / "graph.json"
    out_path.write_text(json.dumps(graph_to_json(graph), indent=2), encoding="utf-8")

    print("=" * 70)
    print("GRAPH BUILT")
    print("=" * 70)
    print(f"  Source              : {source.name} ({len(transactions):,} transactions)")
    print(f"  Nodes               : {graph.number_of_nodes():,} "
          f"({len(wallets):,} wallets, {len(ips):,} IPs)")
    print(f"  Edges               : {graph.number_of_edges():,} "
          f"({len(transfer_edges):,} transfer, "
          f"{graph.number_of_edges() - len(transfer_edges):,} broadcast)")
    print(f"  Individual transfers: {total_transfers:,} "
          f"(collapsed onto {len(transfer_edges):,} wallet pairs)")
    print(f"  Density             : {nx.density(graph):.6f}")

    top = sorted(wallets, key=lambda w: graph.degree(w), reverse=True)[:5]
    print()
    print("  Highest-degree wallets:")
    for w in top:
        print(f"    {w}  degree {graph.degree(w):>4}  "
              f"in {graph.in_degree(w):>3} / out {graph.out_degree(w):>3}")

    print()
    print(f"  -> {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
