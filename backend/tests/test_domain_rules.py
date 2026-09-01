"""Phase 4 detector tests against a small, hand-built graph.

Each pattern below is constructed by hand from a handful of wallets, so a
detector can be confirmed to work - and confirmed not to fire on clean
activity - without depending on the synthetic generator or on any dataset.

Run as a script for a readable report:

    python -m tests.test_domain_rules

or under pytest:

    pytest tests/test_domain_rules.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import networkx as nx

from app.models import Transaction
from app.services.data_generator import USD_PER_BTC
from app.services.graph_builder import build_graph
from app.services.domain_rules import (
    detect_peel_chain,
    detect_rapid_layering,
    detect_round_trip,
    detect_structuring,
    run_all_rules,
    rule_vector,
)

T0 = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)

# Readable stand-ins for wallet addresses. The detectors never parse an
# address, so legible names make a failing test far easier to read.
PEEL = [f"1PEEL{i}" for i in range(6)]
SIDE = [f"1SIDE{i}" for i in range(6)]
SMURF = "1SMURFsource"
TARGETS = [f"1TARGET{i}" for i in range(6)]
LAYER = ["1LAYERa", "1LAYERb", "1LAYERc", "1LAYERd"]
RING = ["1RINGa", "1RINGb", "1RINGc"]
CLEAN = ["1CLEANa", "1CLEANb", "1CLEANc", "1CLEANd"]

_counter = 0


def tx(
    inputs: list[tuple[str, float]],
    outputs: list[tuple[str, float]],
    when: datetime,
    src_ip: str = "203.0.113.10",
) -> Transaction:
    """Build one transaction, deriving the fee from the input/output gap."""
    global _counter
    _counter += 1
    fee = round(sum(a for _, a in inputs) - sum(a for _, a in outputs), 8)
    assert fee > 0, "outputs must leave room for a fee"
    return Transaction(
        txid=f"{_counter:064x}",
        timestamp=when,
        src_ip=src_ip,
        dst_ip="198.51.100.20",
        src_port=50000 + _counter,
        dst_port=8333,
        input_addresses=[a for a, _ in inputs],
        output_addresses=[a for a, _ in outputs],
        input_amounts=[v for _, v in inputs],
        output_amounts=[v for _, v in outputs],
        fee=fee,
        script_type="P2WPKH",
        geo_country="NL",
        asn="AS60781 LEASEWEB-NL",
    )


# --------------------------------------------------------------------------
# Pattern construction
# --------------------------------------------------------------------------


def peel_chain_txs() -> list[Transaction]:
    """5 hops, each forwarding ~90% onward and peeling ~10% to the side."""
    out, amount, when = [], 10.0, T0
    for i in range(5):
        fee = round(amount * 0.001, 8)
        spendable = amount - fee
        forward = round(spendable * 0.9, 8)
        peeled = round(spendable - forward, 8)
        out.append(tx([(PEEL[i], amount)],
                      [(PEEL[i + 1], forward), (SIDE[i], peeled)],
                      when, src_ip="211.34.180.18"))
        amount, when = forward, when + timedelta(minutes=4)
    return out


def structuring_txs() -> list[Transaction]:
    """6 payments of ~$9,000 each in 8 hours: $54,000 moved under a $10k limit."""
    out, when = [], T0
    for i in range(6):
        amount = round(9_000 / USD_PER_BTC, 8)  # 0.15 BTC
        fee = 0.0001
        out.append(tx([(SMURF, round(amount + fee, 8))], [(TARGETS[i], amount)], when))
        when += timedelta(minutes=80)
    return out


def layering_txs() -> list[Transaction]:
    """A -> B -> C -> D, each hop 5 minutes after the last."""
    out, amount, when = [], 3.0, T0
    for src, dst in zip(LAYER, LAYER[1:]):
        fee = 0.001
        forward = round(amount - fee, 8)
        out.append(tx([(src, amount)], [(dst, forward)], when))
        amount, when = forward, when + timedelta(minutes=5)
    return out


def round_trip_txs() -> list[Transaction]:
    """a -> b -> c -> a, returning ~99% of the original amount."""
    out, amount, when = [], 5.0, T0
    ring = [*RING, RING[0]]
    for src, dst in zip(ring, ring[1:]):
        fee = 0.002
        forward = round(amount - fee, 8)
        out.append(tx([(src, amount)], [(dst, forward)], when))
        amount, when = forward, when + timedelta(minutes=25)
    return out


def clean_txs() -> list[Transaction]:
    """Ordinary activity: small, irregular, hours apart, no structure."""
    out = []
    amounts = [0.012, 0.004, 0.031, 0.008, 0.019, 0.006]
    for i, amount in enumerate(amounts):
        src = CLEAN[i % len(CLEAN)]
        dst = CLEAN[(i + 1) % len(CLEAN)]
        out.append(tx([(src, round(amount + 0.0001, 8))], [(dst, amount)],
                      T0 + timedelta(hours=7 * i + 1),
                      src_ip=f"192.0.2.{20 + i}"))
    return out


def build_test_graph() -> nx.DiGraph:
    return build_graph(
        peel_chain_txs() + structuring_txs() + layering_txs()
        + round_trip_txs() + clean_txs()
    )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_peel_chain_detected():
    g = build_test_graph()
    r = detect_peel_chain(g, PEEL[1])
    assert r["triggered"], r
    assert r["evidence"]["chain_length"] >= 3
    assert "peel chain" in r["reason"].lower()


def test_peel_chain_detected_from_tail_of_chain():
    """A wallet late in the chain is as guilty as the one at the head."""
    g = build_test_graph()
    assert detect_peel_chain(g, PEEL[4])["triggered"]


def test_structuring_detected():
    g = build_test_graph()
    r = detect_structuring(g, SMURF)
    assert r["triggered"], r
    assert r["evidence"]["payment_count"] >= 3
    assert r["evidence"]["total_usd"] > 10_000
    assert r["evidence"]["largest_single_usd"] < 10_000


def test_structuring_ignores_amounts_far_below_threshold():
    """Many tiny payments summing over the limit are not structuring."""
    g = build_test_graph()
    assert not detect_structuring(g, CLEAN[0])["triggered"]


def test_rapid_layering_detected():
    g = build_test_graph()
    r = detect_rapid_layering(g, LAYER[0])
    assert r["triggered"], r
    assert r["evidence"]["depth"] >= 3
    assert r["evidence"]["elapsed_minutes"] <= 45


def test_rapid_layering_ignores_slow_movement():
    """The same path spread over days is ordinary, not layering."""
    g = build_test_graph()
    assert not detect_rapid_layering(g, CLEAN[0])["triggered"]


def test_round_trip_detected():
    g = build_test_graph()
    r = detect_round_trip(g, RING[0])
    assert r["triggered"], r
    assert r["evidence"]["retained_fraction"] >= 0.85
    assert RING[1] in r["evidence"]["ring"]


def test_round_trip_respects_similarity_threshold():
    """Demanding near-total return still holds; demanding more than 100% cannot."""
    g = build_test_graph()
    assert detect_round_trip(g, RING[0], similarity_threshold=0.99)["triggered"]
    assert not detect_round_trip(g, RING[0], similarity_threshold=1.5)["triggered"]


def test_peel_side_wallet_detected():
    """The wallets peeled off to are where value leaves the chain."""
    g = build_test_graph()
    r = detect_peel_chain(g, SIDE[1])
    assert r["triggered"], r
    assert r["evidence"]["detector"] == "peel_recipient"


def test_structuring_recipient_detected():
    """A destination of a structured split is part of the pattern."""
    g = build_test_graph()
    r = detect_structuring(g, TARGETS[0])
    assert r["triggered"], r
    assert r["evidence"]["detector"] == "structuring_recipient"
    assert r["evidence"]["funder"] == SMURF


def test_structuring_does_not_recurse_on_single_funder_chain():
    """Regression: a run of single-funder wallets used to recurse until the
    interpreter gave up, and a peel chain is exactly such a run."""
    g = build_test_graph()
    for wallet in (*PEEL, *SIDE[:3]):
        detect_structuring(g, wallet)  # must simply return


def test_round_trip_ignores_two_party_refund():
    """Regression: a -> b -> a is a refund, not a wash.

    Every false positive on the real dataset was a 2-hop bilateral bounce,
    and every genuine wash loop had at least two intermediaries.
    """
    a, b = "1REFUNDa", "1REFUNDb"
    g = build_graph([
        tx([(a, 2.0)], [(b, 1.999)], T0),
        tx([(b, 1.999)], [(a, 1.998)], T0 + timedelta(minutes=30)),
    ])
    assert not detect_round_trip(g, a)["triggered"]
    assert not detect_round_trip(g, b)["triggered"]


def test_round_trip_ignores_a_much_larger_unrelated_return():
    """Regression: comparing only against a lower bound once let 0.004 BTC out
    and 0.019 BTC back be reported as a 475% round trip."""
    a, b, c = "1ASYMa", "1ASYMb", "1ASYMc"
    g = build_graph([
        tx([(a, 0.005)], [(b, 0.004)], T0),
        tx([(b, 0.004)], [(c, 0.0039)], T0 + timedelta(minutes=10)),
        tx([(c, 0.02)], [(a, 0.019)], T0 + timedelta(minutes=20)),
    ])
    r = detect_round_trip(g, a)
    assert not r["triggered"], r


def test_clean_wallets_trigger_nothing():
    g = build_test_graph()
    for wallet in CLEAN:
        assert run_all_rules(g, wallet) == [], f"{wallet} was falsely flagged"


def test_unknown_wallet_does_not_raise():
    g = build_test_graph()
    for r in (detect_peel_chain(g, "1NOPE"), detect_structuring(g, "1NOPE"),
              detect_rapid_layering(g, "1NOPE"), detect_round_trip(g, "1NOPE")):
        assert r["triggered"] is False
        assert "not present" in r["reason"]


def test_evidence_is_json_serialisable():
    """Evidence travels out through the API, so it must survive json.dumps."""
    import json
    g = build_test_graph()
    for wallet in (PEEL[1], SMURF, LAYER[0], RING[0]):
        for r in run_all_rules(g, wallet):
            json.dumps(r)


def test_rule_vector_shape():
    g = build_test_graph()
    v = rule_vector(g, SMURF)
    assert set(v) == {"rule_peel_chain", "rule_structuring",
                      "rule_rapid_layering", "rule_round_trip"}
    assert v["rule_structuring"] == 1
    assert all(x in (0, 1) for x in v.values())


# --------------------------------------------------------------------------
# Readable report
# --------------------------------------------------------------------------


def main() -> None:
    graph = build_test_graph()
    print("=" * 78)
    print("DOMAIN RULE DETECTORS - hand-built graph")
    print("=" * 78)
    print(f"  {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges\n")

    subjects = [
        ("peel chain, hop 1", PEEL[1]),
        ("peel chain, tail", PEEL[4]),
        ("structuring source", SMURF),
        ("layering origin", LAYER[0]),
        ("round trip origin", RING[0]),
        ("clean wallet", CLEAN[0]),
        ("clean wallet", CLEAN[2]),
    ]

    for label, wallet in subjects:
        fired = run_all_rules(graph, wallet)
        print(f"  {label} - {wallet}")
        if not fired:
            print("    (nothing triggered)")
        for r in fired:
            print(f"    [{r['rule']}]")
            for line in _wrap(r["reason"], 68):
                print(f"      {line}")
            evidence = r["evidence"]
            keys = [k for k in evidence if k not in ("chain", "hops", "path")]
            shown = ", ".join(f"{k}={evidence[k]!r}" for k in keys[:4])
            print(f"      evidence: {shown}")
        print()

    print("=" * 78)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for w in words:
        if len(current) + len(w) + 1 > width:
            lines.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    main()
