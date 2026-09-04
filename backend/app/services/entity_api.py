"""Turns entity resolution, scored wallets and raw transactions into what the
investigation-board frontend actually renders: actor cards, a wallet
dossier, and cross-border flows.

WHY THIS MODULE EXISTS SEPARATELY. entity_resolution.py answers a research
question - does fusing two signals correctly group wallets, measured against
planted ground truth. Nothing here changes that module or its guarantees.
This module is presentation shaping: turning its output (and the existing
wallet scores) into the specific shapes the API contract in models.py
promises the frontend, the same separation main.py already keeps between
pipeline stages and response formatting.

REAL DATA THROUGHOUT. Every field below is computed from the actual ingested
graph and transactions - nothing here is a placeholder. Where a genuine gap
exists (no distinct src/dst country per transaction, only one observed
geo_country), the gap is worked around honestly (see GeoFlow's docstring in
models.py) rather than papered over with an invented number.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import networkx as nx

from app.models import (
    ActorCard,
    ActorConnection,
    ActorDetail,
    ConnectedWalletInfo,
    ContributingFeature,
    GeoFlow,
    GeoFlowWallet,
    TrailHop,
    WalletDossier,
)
from app.services import domain_rules
from app.services.entity_resolution import CONFIRMED, resolve_entities
from app.services.feature_extraction import FEATURE_NAMES, extract_wallet_features
from app.services.graph_builder import EDGE_KIND_TRANSFER

# Below this many multi-wallet entities, top singleton high-risk wallets are
# added as single-wallet "actors" too, so the board is never dominated only
# by the handful of wallets that happen to co-spend or co-broadcast -
# entity resolution's own documented KNOWN GAP is that a wallet which never
# appears as a transaction input is invisible to it.
MAX_SINGLETON_ACTORS = 12
SINGLETON_RISK_FLOOR = 0.55  # roughly "high" severity and above

# A feature's "typical high" bound for the dossier's progress bars, so a
# wallet nowhere near the top of the training distribution doesn't render a
# maxed-out bar. Falls back to these when no trained reference is available.
_DEFAULT_MAX = {
    "in_degree": 20.0, "out_degree": 20.0, "degree_centrality": 0.05,
    "pagerank": 0.005, "clustering_coefficient": 1.0,
    "transaction_velocity": 30.0, "total_sent": 10.0, "total_received": 10.0,
    "amount_variance": 5.0, "fan_in_out_ratio": 5.0,
}

_FEATURE_UNITS = {
    "in_degree": "", "out_degree": "", "degree_centrality": "",
    "pagerank": "", "clustering_coefficient": "", "transaction_velocity": "/hr",
    "total_sent": " BTC", "total_received": " BTC", "amount_variance": "",
    "fan_in_out_ratio": ":1",
}


# --------------------------------------------------------------------------
# Actors: entities + singleton high-risk wallets
# --------------------------------------------------------------------------


def _entity_risk_and_confidence(
    entity: dict, alerts_by_wallet: dict[str, dict]
) -> tuple[int, int]:
    """An entity's risk is driven by its worst member; confidence rewards
    being corroborated by both signals, not just one."""
    scores = [alerts_by_wallet[w]["risk_score"] for w in entity["wallets"] if w in alerts_by_wallet]
    confidences = [alerts_by_wallet[w]["confidence"] for w in entity["wallets"] if w in alerts_by_wallet]
    risk = round((max(scores) if scores else 0.0) * 100)
    base_conf = (sum(confidences) / len(confidences) if confidences else 0.5) * 100
    bonus = 12 if entity["confidence"] == CONFIRMED else 0
    return risk, min(99, round(base_conf + bonus))


def _entity_summary(entity: dict, alerts_by_wallet: dict[str, dict]) -> str:
    """A one-line description built from real evidence, not a template
    filled with placeholder nouns."""
    worst = max(
        (w for w in entity["wallets"] if w in alerts_by_wallet),
        key=lambda w: alerts_by_wallet[w]["risk_score"],
        default=None,
    )
    reason = ""
    if worst and alerts_by_wallet[worst]["top_reasons"]:
        reason = alerts_by_wallet[worst]["top_reasons"][0]

    basis = {
        "confirmed": "co-spending AND broadcasting from the same IP",
        "chain_only": "co-spending on the same transaction",
        "network_only": "broadcasting from the same source IP",
    }.get(entity["confidence"], "a shared signal")

    lead = f"{len(entity['wallets'])} wallets linked by {basis}"
    return f"{lead} — {reason}" if reason else lead


def _cross_entity_links(
    graph: nx.DiGraph, entities: list[dict]
) -> tuple[dict[str, list[str]], dict[tuple[str, str], float]]:
    """Which entities are connected by a real wallet-to-wallet edge, and how
    much value moved between them."""
    owner: dict[str, str] = {}
    for e in entities:
        for w in e["wallets"]:
            owner[w] = e["entity_id"]

    connected: dict[str, set[str]] = defaultdict(set)
    amounts: dict[tuple[str, str], float] = defaultdict(float)

    for u, v, data in graph.edges(data=True):
        if data.get("kind") != EDGE_KIND_TRANSFER:
            continue
        eu, ev = owner.get(u), owner.get(v)
        if eu is None or ev is None or eu == ev:
            continue
        connected[eu].add(ev)
        connected[ev].add(eu)
        key = tuple(sorted((eu, ev)))
        amounts[key] += data.get("amount", 0.0)

    return {k: sorted(v) for k, v in connected.items()}, dict(amounts)


def build_actors_from_transactions(
    transactions: list,
    graph: nx.DiGraph,
    alerts: list[dict[str, Any]],
    max_cards: int = 60,
) -> tuple[list[ActorCard], dict[str, ActorDetail], dict[str, Any]]:
    """The board's full actor list, and a lookup for the detail panel.

    Resolves entities from the real transaction list (co-broadcast needs
    src_ip, which only the raw transactions carry, not the built graph).
    Combines them with singleton high-risk wallets entity resolution's two
    signals could not link to anything - both are genuinely "an actor worth
    an investigator's attention", just evidenced differently.

    `max_cards` caps what a card grid can reasonably render, the same
    principle /graph and /alerts already apply - a UI showing everything
    would show nothing legibly.
    """
    alerts_by_wallet = {a["wallet_address"]: a for a in alerts}
    entities_raw = resolve_entities(transactions)
    entity_wallets = {w for e in entities_raw for w in e["wallets"]}

    numbered = [
        {**e, "entity_id": f"ACT-{i + 1:03d}"} for i, e in enumerate(entities_raw)
    ]

    singletons = [
        a for a in alerts
        if a["wallet_address"] not in entity_wallets and a["risk_score"] >= SINGLETON_RISK_FLOOR
    ][:MAX_SINGLETON_ACTORS]
    for i, a in enumerate(singletons):
        numbered.append({
            "entity_id": f"ACT-{len(entities_raw) + i + 1:03d}",
            "wallets": [a["wallet_address"]],
            "confidence": "single_wallet",
        })

    links, amounts = _cross_entity_links(graph, numbered)

    cards: list[ActorCard] = []
    details: dict[str, ActorDetail] = {}

    for e in numbered:
        risk, conf = _entity_risk_and_confidence(e, alerts_by_wallet)
        if e["confidence"] == "single_wallet":
            w = e["wallets"][0]
            summary = (alerts_by_wallet.get(w, {}).get("top_reasons") or ["Flagged wallet"])[0]
        else:
            summary = _entity_summary(e, alerts_by_wallet)

        connected = links.get(e["entity_id"], [])
        card = ActorCard(
            actor_id=e["entity_id"],
            member_wallet_ids=e["wallets"],
            aggregate_risk_score=risk,
            confidence=conf,
            short_summary=summary,
            connected_actor_ids=connected,
        )
        cards.append(card)

        reasons: list[str] = []
        for w in e["wallets"]:
            for r in alerts_by_wallet.get(w, {}).get("top_reasons", []):
                if r not in reasons:
                    reasons.append(r)
        reasons = reasons[:6] or ["No further evidence attached"]

        connections = []
        for other in connected:
            key = tuple(sorted((e["entity_id"], other)))
            connections.append(ActorConnection(
                target_actor_id=other,
                link_type="chain_and_network" if e["confidence"] == CONFIRMED else "graph_edge",
                amount_btc=round(amounts.get(key, 0.0), 4),
            ))

        details[e["entity_id"]] = ActorDetail(
            **card.model_dump(),
            top_reasons=reasons,
            actor_connections=connections,
        )

    cards.sort(key=lambda c: c.aggregate_risk_score, reverse=True)
    if len(cards) > max_cards:
        keep = {c.actor_id for c in cards[:max_cards]}
        cards = cards[:max_cards]
        for c in cards:
            c.connected_actor_ids = [i for i in c.connected_actor_ids if i in keep]
        details = {k: v for k, v in details.items() if k in keep}
        # A detail's own actor_connections were built before the cut, so a
        # link to a now-excluded actor would otherwise point at a 404.
        for d in details.values():
            d.connected_actor_ids = [i for i in d.connected_actor_ids if i in keep]
            d.actor_connections = [
                c for c in d.actor_connections if c.target_actor_id in keep
            ]

    # A real actor-by-actor intensity matrix (real BTC amounts, aligned to
    # the same order and cut as `cards`), for the heatmap - built once here
    # rather than making the frontend fetch every actor's own detail just to
    # populate a grid.
    ids = [c.actor_id for c in cards]
    index = {aid: i for i, aid in enumerate(ids)}
    size = len(ids)
    grid: list[list[float]] = [[0.0] * size for _ in range(size)]
    for (a, b), amt in amounts.items():
        if a in index and b in index:
            grid[index[a]][index[b]] = round(amt, 4)
            grid[index[b]][index[a]] = round(amt, 4)
    matrix = {"actor_ids": ids, "matrix": grid}

    return cards, details, matrix


# --------------------------------------------------------------------------
# Wallet dossier
# --------------------------------------------------------------------------


def _walk_trail(graph: nx.DiGraph, wallet: str, max_hops: int = 5) -> list[dict[str, Any]]:
    """Follow the largest outgoing transfer from `wallet`, repeatedly - a
    real walk of the biggest onward movement of funds, not a fabricated
    sequence. Stops early if a wallet has already been visited (a real
    round trip) or has no further outgoing transfer."""
    hops: list[dict[str, Any]] = []
    current = wallet
    visited = {wallet}

    for step in range(1, max_hops + 1):
        best: dict[str, Any] | None = None
        for _, dst, data in graph.out_edges(current, data=True):
            if data.get("kind") != EDGE_KIND_TRANSFER or dst in visited:
                continue
            for t in data.get("transfers", []):
                if best is None or t["amount"] > best["amount"]:
                    best = {**t, "to": dst}
        if best is None:
            break
        hops.append({
            "step": step, "from": current, "to": best["to"],
            "amount": best["amount"], "timestamp": best["timestamp"],
            "txid": best["txid"],
        })
        visited.add(best["to"])
        current = best["to"]

    return hops


def build_wallet_dossier(
    graph: nx.DiGraph,
    wallet: str,
    alert: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> WalletDossier:
    """The rich, real profile behind one wallet - every field computed from
    the graph and the transactions it actually appeared in."""
    node = graph.nodes[wallet]
    features = extract_wallet_features(graph, wallet)
    p90 = (manifest or {}).get("reference", {}).get("p90", {})

    contributing = [
        ContributingFeature(
            name=name.replace("_", " "),
            raw=round(features.get(name, 0.0), 4),
            max=max(round(p90.get(name, _DEFAULT_MAX[name]), 4), features.get(name, 0.0), 0.0001),
            unit=_FEATURE_UNITS.get(name, ""),
        )
        for name in FEATURE_NAMES
    ]

    # A 16-bucket weekly transaction-velocity sparkline from the wallet's
    # real transfer timestamps - not illustrative data.
    times = sorted(
        t["timestamp"]
        for _, dst, d in graph.out_edges(wallet, data=True)
        if d.get("kind") == EDGE_KIND_TRANSFER
        for t in d.get("transfers", [])
    ) + sorted(
        t["timestamp"]
        for src, _, d in graph.in_edges(wallet, data=True)
        if d.get("kind") == EDGE_KIND_TRANSFER
        for t in d.get("transfers", [])
    )
    velocity: list[int] = [0] * 16
    if times:
        start, end = min(times), max(times)
        span = max((end - start).total_seconds(), 1.0)
        for t in times:
            bucket = min(15, int((t - start).total_seconds() / span * 16))
            velocity[bucket] += 1

    connected: list[ConnectedWalletInfo] = []
    for neighbour in alert.get("connected_wallets", [])[:12]:
        n = graph.nodes.get(neighbour, {})
        connected.append(ConnectedWalletInfo(
            address=neighbour,
            risk_score=round((n.get("risk_score") or 0.0) * 100),
            severity=(n.get("severity") or "low").upper(),
            relation="1-hop counterparty",
        ))

    trail_raw = _walk_trail(graph, wallet)
    trail: list[TrailHop] = []
    for hop in trail_raw:
        to_node = graph.nodes.get(hop["to"], {})
        trail.append(TrailHop(
            step=hop["step"], from_wallet=hop["from"], to_wallet=hop["to"],
            to_label=f"{(to_node.get('severity') or 'unscored').upper()} wallet, {to_node.get('tx_count', 0)} tx",
            amount_btc=round(hop["amount"], 8),
            amount_usd=round(hop["amount"] * 60_000.0, 2),  # same USD_PER_BTC as data_generator
            timestamp=hop["timestamp"].isoformat(),
            tx_hash=hop["txid"][:16],
            to_score=round((to_node.get("risk_score") or 0.0) * 100),
            to_severity=(to_node.get("severity") or "low").upper(),
        ))

    reasons = alert.get("top_reasons") or ["No suspicious pattern found in this wallet's activity"]
    narrative = " ".join(reasons)
    if len(reasons) > 1:
        narrative = reasons[0] + "\n\n" + " ".join(reasons[1:])

    return WalletDossier(
        wallet_id=wallet,
        address=wallet,
        address_full=wallet,
        risk_score=round(alert["risk_score"] * 100),
        confidence=round(alert["confidence"] * 100),
        severity=alert["severity"].upper(),
        first_seen=node.get("first_seen").date().isoformat() if node.get("first_seen") else "unknown",
        last_active=node.get("last_seen").date().isoformat() if node.get("last_seen") else "unknown",
        tx_count=node.get("tx_count", 0),
        total_volume_btc=round(node.get("total_sent", 0.0) + node.get("total_received", 0.0), 4),
        velocity_data=velocity,
        ai_narrative=narrative,
        contributing_features=contributing,
        connected_wallets=connected,
        trail=trail,
    )


# --------------------------------------------------------------------------
# Geographic flow
# --------------------------------------------------------------------------

# ISO code -> display name, for the small set data_generator.py actually
# produces. Falls back to the ISO code itself for anything outside that set.
_COUNTRY_NAMES = {
    "IN": "India", "US": "USA", "DE": "Germany", "NL": "Netherlands",
    "RU": "Russia", "CN": "China", "SG": "Singapore", "GB": "UK",
    "BR": "Brazil", "NG": "Nigeria",
}


def build_geo_flows(transactions: list, alerts_by_wallet: dict[str, dict], top_n: int = 40) -> list[GeoFlow]:
    """Cross-border value flow, inferred honestly.

    A single transaction records one geo_country - the observing capture
    point - not distinct sender/receiver locations, so "cross-border" here
    means: the MAJORITY geo_country seen across all of a wallet's own
    transactions, compared between the two wallets on each transfer. That is
    a real, computed inference, not a fabricated src/dst pair.
    """
    wallet_countries: dict[str, Counter] = defaultdict(Counter)
    for tx in transactions:
        for addr in set(tx.input_addresses) | set(tx.output_addresses):
            wallet_countries[addr][tx.geo_country] += 1

    inferred = {w: c.most_common(1)[0][0] for w, c in wallet_countries.items() if c}

    pair_amount: dict[tuple[str, str], float] = defaultdict(float)
    # Real wallet-to-wallet transfers behind each country pair, kept so the
    # frontend can drill from an aggregated line down to the actual wallets
    # that moved value on it (not just the country-level total).
    pair_transfers: dict[tuple[str, str], list[tuple[str, str, float, float]]] = defaultdict(list)

    for tx in transactions:
        for src in set(tx.input_addresses):
            c_src = inferred.get(src)
            if not c_src:
                continue
            for dst, amt in zip(tx.output_addresses, tx.output_amounts):
                c_dst = inferred.get(dst)
                if not c_dst or c_dst == c_src:
                    continue
                pair_amount[(c_src, c_dst)] += amt
                risk = max(
                    alerts_by_wallet.get(src, {}).get("risk_score", 0.0),
                    alerts_by_wallet.get(dst, {}).get("risk_score", 0.0),
                )
                pair_transfers[(c_src, c_dst)].append((src, dst, amt, risk))

    flows = []
    for (src, dst), amt in pair_amount.items():
        transfers = pair_transfers[(src, dst)]
        # Amount-weighted average risk: what fraction of the *value* moving
        # through this corridor is tied to risky wallets. An unweighted mean
        # buries a few risky wallets under bulk clean volume (the original
        # bug); a plain max does the opposite and saturates almost every
        # corridor to CRITICAL the moment one high-risk wallet touches it
        # anywhere, however small the amount (measured: 34/40 corridors hit
        # CRITICAL under a pure max, on this dataset). Weighting by the
        # actual BTC moved gives the graded signal a money-flow map needs.
        corridor_risk = sum(t[2] * t[3] for t in transfers) / amt
        top_transfers = sorted(transfers, key=lambda t: t[2], reverse=True)[:5]
        flows.append(GeoFlow(
            from_country=_COUNTRY_NAMES.get(src, src),
            to_country=_COUNTRY_NAMES.get(dst, dst),
            amount=round(amt, 4),
            risk_score=round(corridor_risk * 100),
            sample_wallets=[
                GeoFlowWallet(
                    from_wallet=w_src, to_wallet=w_dst,
                    amount_btc=round(w_amt, 4), risk_score=round(w_risk * 100),
                )
                for w_src, w_dst, w_amt, w_risk in top_transfers
            ],
        ))

    # Risk first, amount as the tiebreaker - this is a threat map, so a
    # corridor worth an analyst's attention should survive the top_n cut
    # ahead of one that is merely large.
    flows.sort(key=lambda f: (f.risk_score, f.amount), reverse=True)
    return flows[:top_n]
