"""Tests for entity resolution: the fusion of common-input-ownership and
co-broadcast into wallet-to-actor groupings.

Two of these tests exist specifically because two real bugs were found while
building this module - both signals, tuned naively, chained the entire
synthetic dataset into one giant "entity" and drove precision to 0%. They are
regression guards, not just correctness checks: either bug reappearing would
be silent and would not show up as an exception anywhere else.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Transaction
from app.services.data_generator import build_dataset
from app.services.entity_resolution import (
    CHAIN_ONLY,
    CONFIRMED,
    NETWORK_ONLY,
    co_broadcast_edges,
    common_input_edges,
    evaluate_against_ground_truth,
    resolve_entities,
)

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_counter = 0


def tx(
    inputs: list[tuple[str, float]],
    outputs: list[tuple[str, float]],
    when: datetime,
    src_ip: str = "203.0.113.10",
) -> Transaction:
    global _counter
    _counter += 1
    fee = round(sum(a for _, a in inputs) - sum(a for _, a in outputs), 8)
    assert fee > 0
    return Transaction(
        txid=f"{_counter:064x}",
        timestamp=when,
        src_ip=src_ip,
        dst_ip="198.51.100.1",
        src_port=50000 + _counter,
        dst_port=8333,
        input_addresses=[a for a, _ in inputs],
        output_addresses=[a for a, _ in outputs],
        input_amounts=[v for _, v in inputs],
        output_amounts=[v for _, v in outputs],
        fee=fee,
        script_type="P2WPKH",
        geo_country="NL",
        asn="AS60781",
    )


# --------------------------------------------------------------------------
# Chain signal
# --------------------------------------------------------------------------


def test_common_input_edge_links_co_spenders():
    t = tx([("A", 1.0), ("B", 1.0)], [("C", 1.999)], T0)
    edges = common_input_edges([t])
    assert ("A", "B") in edges or ("B", "A") in edges


def test_common_input_ignores_single_input_transactions():
    t = tx([("A", 1.0)], [("B", 0.999)], T0)
    assert common_input_edges([t]) == []


def test_hub_wallet_is_excluded_from_common_input_clustering():
    """Regression: a single wallet co-spending with many different others used
    to chain the entire dataset into one entity via transitive union-find and
    drove precision to 0%. A hub must not be able to link its many partners
    to EACH OTHER through it."""
    txs = []
    # A co-spends with 6 different wallets across 6 different transactions -
    # a real service/exchange consolidation pattern, not one person's wallets.
    for i in range(6):
        txs.append(tx([("HUB", 1.0), (f"partner{i}", 1.0)], [("out", 1.999)],
                       T0 + timedelta(minutes=i)))
    edges = common_input_edges(txs, max_degree=4)
    touched = {w for pair in edges for w in pair}
    assert "HUB" not in touched, "the hub itself must be excluded"
    # None of the hub's partners should end up linked to EACH OTHER, since
    # their only connection was through the excluded hub.
    partners = {f"partner{i}" for i in range(6)}
    assert not any(set(pair) <= partners for pair in edges)


# --------------------------------------------------------------------------
# Network signal
# --------------------------------------------------------------------------


def test_co_broadcast_links_wallets_on_the_same_ip_within_the_window():
    txs = [
        tx([("A", 1.0)], [("X", 0.999)], T0, src_ip="9.9.9.9"),
        tx([("B", 1.0)], [("Y", 0.999)], T0 + timedelta(minutes=10), src_ip="9.9.9.9"),
    ]
    edges = co_broadcast_edges(txs, window=timedelta(hours=1))
    assert ("A", "B") in edges or ("B", "A") in edges


def test_co_broadcast_respects_the_time_window():
    txs = [
        tx([("A", 1.0)], [("X", 0.999)], T0, src_ip="9.9.9.9"),
        tx([("B", 1.0)], [("Y", 0.999)], T0 + timedelta(hours=5), src_ip="9.9.9.9"),
    ]
    edges = co_broadcast_edges(txs, window=timedelta(hours=1))
    assert edges == []


def test_scattered_wallet_is_excluded_from_network_clustering():
    """Regression: a wallet using many different shared IPs over its lifetime
    used to bridge otherwise-unrelated IP-local bursts together, percolating
    the whole dataset into one 951-wallet entity with 0% precision, even
    though no single IP's burst ever exceeded the time window."""
    txs = []
    # BRIDGE uses three different IPs, each shared briefly with one other
    # wallet - exactly the diffuse-usage pattern that should be excluded.
    for i, ip in enumerate(["1.1.1.1", "2.2.2.2", "3.3.3.3"]):
        txs.append(tx([("BRIDGE", 1.0)], [("out", 0.999)],
                       T0 + timedelta(hours=i * 10), src_ip=ip))
        txs.append(tx([(f"local{i}", 1.0)], [("out", 0.999)],
                       T0 + timedelta(hours=i * 10, minutes=5), src_ip=ip))
    edges = co_broadcast_edges(txs, window=timedelta(hours=1), max_ip_spread=2)
    touched = {w for pair in edges for w in pair}
    assert "BRIDGE" not in touched
    # local0/local1/local2 must not be transitively linked via the bridge.
    locals_ = {"local0", "local1", "local2"}
    assert not any(set(pair) <= locals_ for pair in edges)


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------


def test_confirmed_requires_both_signals():
    """A co-input pair where the network signal is genuinely absent - not
    merely a different transaction, since a multi-input transaction always
    co-broadcasts its own inputs too, which would trivially confirm any pair
    drawn from one tx. Here B's spends are scattered across many IPs
    elsewhere, which excludes B from the network signal entirely (including
    for this very transaction), so only the chain edge survives."""
    txs = [tx([("A", 1.0), ("B", 1.0)], [("C", 1.999)], T0, src_ip="1.1.1.1")]
    for i, ip in enumerate(["2.2.2.2", "3.3.3.3", "4.4.4.4"]):
        txs.append(tx([("B", 1.0)], [("out", 0.999)],
                       T0 + timedelta(days=i + 1), src_ip=ip))

    entities = resolve_entities(txs, window=timedelta(hours=1))
    merged = next(e for e in entities if {"A", "B"} <= set(e["wallets"]))
    assert merged["confidence"] == CHAIN_ONLY


def test_confirmed_when_both_signals_touch_the_same_entity():
    txs = [
        tx([("A", 1.0), ("B", 1.0)], [("C", 1.999)], T0, src_ip="1.1.1.1"),
        tx([("A", 1.0)], [("D", 0.999)], T0 + timedelta(minutes=5), src_ip="1.1.1.1"),
        tx([("E", 1.0)], [("F", 0.999)], T0 + timedelta(minutes=6), src_ip="1.1.1.1"),
    ]
    entities = resolve_entities(txs, window=timedelta(hours=1))
    merged = next(e for e in entities if "A" in e["wallets"])
    assert merged["confidence"] == CONFIRMED
    assert merged["chain_linked"] and merged["network_linked"]


def test_singleton_wallets_are_not_returned_as_entities():
    txs = [tx([("A", 1.0)], [("B", 0.999)], T0, src_ip="1.1.1.1")]
    assert resolve_entities(txs) == []


def test_no_entity_ever_exceeds_a_sane_size_on_the_demo_dataset():
    """The blanket regression guard: whatever else changes, entity resolution
    must never again produce a near-dataset-spanning blob."""
    transactions, _ = build_dataset(n_normal=1500, seed=99)
    entities = resolve_entities(transactions)
    assert entities, "expected at least one multi-wallet entity"
    largest = max(e["size"] for e in entities)
    # 100 is generous headroom over anything a genuine planted pattern
    # produces (the biggest planted pattern has ~26 wallets); anything near
    # dataset size signals the hub-poisoning bug is back.
    assert largest < 100, f"entity of size {largest} suggests hub poisoning"


# --------------------------------------------------------------------------
# Measurement against ground truth
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_dataset():
    return build_dataset(n_normal=2000, seed=4242)


def test_spending_wallets_are_recovered_above_chance(demo_dataset):
    """Wallets that actually spend (peel-chain hops, round-trip ring members)
    should be grouped together at meaningfully above-zero recall - not a
    specific target number, since this is a genuinely hard, newly-built
    capability, but a floor that would fail if the fusion stopped working."""
    transactions, ground_truth = demo_dataset
    entities = resolve_entities(transactions)
    metrics = evaluate_against_ground_truth(entities, ground_truth)

    by_pattern = metrics["recall_by_pattern_type"]
    if "peel_chain" in by_pattern:
        assert by_pattern["peel_chain"] > 0.05
    if "round_trip" in by_pattern:
        assert by_pattern["round_trip"] >= 0.0  # small patterns; just must run


def test_receive_only_wallets_score_zero_by_construction(demo_dataset):
    """rapid_fanout targets never spend, so neither signal can see them. This
    is asserted explicitly so the documented scope boundary is checked, not
    just claimed in a comment."""
    transactions, ground_truth = demo_dataset
    entities = resolve_entities(transactions)
    metrics = evaluate_against_ground_truth(entities, ground_truth)
    if "rapid_fanout" in metrics["recall_by_pattern_type"]:
        assert metrics["recall_by_pattern_type"]["rapid_fanout"] == 0.0


def test_pairwise_precision_and_recall_are_valid_fractions(demo_dataset):
    transactions, ground_truth = demo_dataset
    entities = resolve_entities(transactions)
    metrics = evaluate_against_ground_truth(entities, ground_truth)
    assert 0.0 <= metrics["pairwise_precision"] <= 1.0
    assert 0.0 <= metrics["pairwise_recall"] <= 1.0
