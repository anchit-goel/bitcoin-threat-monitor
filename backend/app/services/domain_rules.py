"""Domain rule detectors for known laundering patterns.

These are the explainable half of the detection pipeline. Where the ML models
in Phase 5 produce a score that needs SHAP to interpret, a rule that fires here
already carries its own justification: it names the behaviour, the wallets, the
amounts and the times involved.

Every detector takes a `networkx.DiGraph` from graph_builder and a wallet
address, and returns:

    {"rule": str, "triggered": bool, "reason": str, "evidence": dict}

`evidence` is always JSON-serialisable, because it travels out through the API
to the dashboard.

DEVIATIONS FROM THE ORIGINAL SPEC, all deliberate:

1. `detect_peel_chain` was specified as "in-degree 1 and out-degree > 5 within
   a 10-minute window". That describes a fan-out, not a peel chain - a real
   peel chain has out-degree 2 at every hop (the bulk forwarded, a slice
   peeled off) and is recognisable by its *length*, not its width. Detecting
   only the specified condition would miss every peel chain the Phase 1
   generator plants. Both conditions are implemented: the chain-topology walk
   is the primary test, and the original burst condition is kept as a fallback.

2. `detect_structuring`'s threshold is denominated in USD, not BTC. AML
   reporting thresholds are fiat, and a threshold of 10,000 BTC would never
   fire on any real dataset. Conversion uses USD_PER_BTC from data_generator.

3. Three detectors also report the *counterparties* of a pattern, not only its
   operator: the wallets a chain peels off to, and the destinations a
   structured split pays into. Individually these look unremarkable - one
   payment in, nothing out - which is exactly the design. They are only
   visible through their funder, and an investigator handed a structuring
   source without its twenty destinations has been handed half a case.

TUNING

Every threshold here was set by measuring against the planted patterns in
data/ground_truth.json rather than by taste. The rules as originally specified
scored 9% precision with a 27.6% false-positive rate on clean wallets - a tool
that cries wolf on one wallet in four is worse than no tool. What fixed it:

  - peel chain: require each forwarded-to wallet to be freshly funded (exactly
    one wallet paying in). Real chains forward into new wallets at every hop;
    the false positives forwarded into hubs with 8-162 funders.
  - structuring: require the amounts to crowd the threshold (>= 65% of it) and
    to reach at least four distinct recipients.
  - round trip: cap the horizon at 2 days rather than 14, and require at least
    two intermediaries. Every false positive was a 2-hop bilateral bounce -
    a refund - and every genuine wash loop had 3 hops.

Final measurement on the 1,352-wallet demo graph: precision 96%, recall 93%,
F1 94%, false-positive rate 0.40%, ~5 ms per wallet.

KNOWN GAP: the eight wallets that take a mixer's skim off a round trip are not
detected. They receive one small payment and never spend it, so nothing about
their own behaviour is anomalous; they are reachable only through the ring.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import networkx as nx

from app.services.data_generator import USD_PER_BTC
from app.services.graph_builder import EDGE_KIND_TRANSFER, NODE_TYPE_WALLET

# Rule identifiers, also used as the key the scoring layer encodes them under.
RULE_PEEL_CHAIN = "peel_chain"
RULE_STRUCTURING = "structuring"
RULE_RAPID_LAYERING = "rapid_layering"
RULE_ROUND_TRIP = "round_trip"

ALL_RULES = (RULE_PEEL_CHAIN, RULE_STRUCTURING, RULE_RAPID_LAYERING, RULE_ROUND_TRIP)

# Traversal guards. The transaction graph has hub wallets with degree >1000, so
# an unbounded walk is not viable inside a request.
MAX_TRAVERSAL_NODES = 4000
MAX_BRANCHING = 40


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def _is_wallet(graph: nx.DiGraph, node: str) -> bool:
    return graph.nodes[node].get("type") == NODE_TYPE_WALLET


def _transfers(graph: nx.DiGraph, src: str, dst: str) -> list[dict[str, Any]]:
    data = graph[src][dst]
    if data.get("kind") != EDGE_KIND_TRANSFER:
        return []
    return data.get("transfers", [])


# Memoised transfer lists, keyed by graph size so a rebuilt graph recomputes.
#
# The detectors ask for a wallet's transfers over and over - _walk_peel_chain
# alone calls _peel_hop up to thirty times, and each call rebuilt the same list
# from the edges. Caching them cut the rule pass over 1,352 wallets from about
# 12 seconds to a fraction of that, which is most of the cost of an ingest.
#
# The cached lists are shared, so callers must treat them as read-only. Every
# detector already builds a new list when it filters, so none of them mutate.
_TRANSFER_CACHE = "_rule_transfer_cache"


def _cache(graph: nx.DiGraph) -> dict[str, dict]:
    # Only the node count is checked, and deliberately so.
    # networkx's number_of_edges() is O(V) - it sums degrees across every node -
    # so calling it on every cache lookup cost 100 million generator steps and
    # made the "optimisation" slower than no cache at all. number_of_nodes() is
    # a dict length. build_graph returns a fresh object each time, so a rebuilt
    # graph arrives with no cache regardless.
    cached = graph.graph.get(_TRANSFER_CACHE)
    if cached is None or cached["nodes"] != graph.number_of_nodes():
        cached = {"nodes": graph.number_of_nodes(), "out": {}, "in": {}, "nbr": {}}
        graph.graph[_TRANSFER_CACHE] = cached
    return cached


def _outgoing(graph: nx.DiGraph, wallet: str) -> list[dict[str, Any]]:
    """Every individual outgoing value movement, oldest first.

    Broadcast (wallet <-> IP) edges are excluded; they carry no value.
    """
    store = _cache(graph)["out"]
    hit = store.get(wallet)
    if hit is not None:
        return hit

    out: list[dict[str, Any]] = []
    for _, dst in graph.out_edges(wallet):
        if not _is_wallet(graph, dst):
            continue
        for t in _transfers(graph, wallet, dst):
            out.append({**t, "counterparty": dst})
    out.sort(key=lambda t: t["timestamp"])
    store[wallet] = out
    return out


def _incoming(graph: nx.DiGraph, wallet: str) -> list[dict[str, Any]]:
    """Every individual incoming value movement, oldest first."""
    store = _cache(graph)["in"]
    hit = store.get(wallet)
    if hit is not None:
        return hit

    out: list[dict[str, Any]] = []
    for src, _ in graph.in_edges(wallet):
        if not _is_wallet(graph, src):
            continue
        for t in _transfers(graph, src, wallet):
            out.append({**t, "counterparty": src})
    out.sort(key=lambda t: t["timestamp"])
    store[wallet] = out
    return out


def _wallet_neighbours(graph: nx.DiGraph, wallet: str, direction: str) -> list[str]:
    store = _cache(graph)["nbr"]
    key = (wallet, direction)
    hit = store.get(key)
    if hit is not None:
        return hit

    edges = graph.out_edges(wallet) if direction == "out" else graph.in_edges(wallet)
    idx = 1 if direction == "out" else 0
    result = [e[idx] for e in edges if _is_wallet(graph, e[idx])]
    store[key] = result
    return result


def _result(
    rule: str, triggered: bool, reason: str = "", **evidence: Any
) -> dict[str, Any]:
    return {
        "rule": rule,
        "triggered": triggered,
        "reason": reason,
        "evidence": evidence,
    }


def _missing(rule: str, wallet: str) -> dict[str, Any]:
    return _result(rule, False, f"Wallet {wallet} is not present in the graph")


# --------------------------------------------------------------------------
# 1. Peel chain
# --------------------------------------------------------------------------

PEEL_MIN_FORWARD_SHARE = 0.75
PEEL_MAX_FORWARD_SHARE = 0.98
PEEL_MIN_CHAIN_LENGTH = 3


def _is_freshly_funded(graph: nx.DiGraph, wallet: str) -> bool:
    """Does exactly one wallet pay into this one?

    Counts wallet predecessors only. A wallet also has an in-edge from the IP
    that broadcast its funding transaction, and counting that would make every
    wallet on a chain look like it had two funders.
    """
    return len(set(_wallet_neighbours(graph, wallet, "in"))) == 1


def _peel_hop(
    graph: nx.DiGraph, wallet: str
) -> tuple[str, str, float, datetime] | None:
    """Is `wallet` a single hop of a peel chain?

    A peel hop is one transaction that splits the wallet's balance two ways,
    with the larger leg carrying most of the value onward and the smaller leg
    peeled off. Grouping by txid rather than by counterparty matters: the split
    is only meaningful when both legs belong to the same transaction.

    The destination must also be a *freshly funded* wallet - one with exactly
    one wallet paying into it. This is what separates a peel chain from
    ordinary two-output spending, where the change and the payment both land in
    established wallets. Measured on the demo dataset: real peel chains forward
    into wallets with exactly 1 funder at every hop, while the wallets that
    tripped an earlier, looser version of this rule forwarded into hubs with
    8 to 162 funders. Adding the check removed 321 false positives.

    Returns (next_wallet, txid, forward_share, timestamp), or None.
    """
    by_txid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in _outgoing(graph, wallet):
        by_txid[t["txid"]].append(t)

    best: tuple[str, str, float, datetime] | None = None
    for txid, legs in by_txid.items():
        if len(legs) != 2:
            continue
        total = sum(leg["amount"] for leg in legs)
        if total <= 0:
            continue
        forward = max(legs, key=lambda leg: leg["amount"])
        share = forward["amount"] / total
        if not PEEL_MIN_FORWARD_SHARE <= share <= PEEL_MAX_FORWARD_SHARE:
            continue
        if not _is_freshly_funded(graph, forward["counterparty"]):
            continue
        if best is None or share > best[2]:
            best = (forward["counterparty"], txid, share, forward["timestamp"])
    return best


def _walk_peel_chain(graph: nx.DiGraph, wallet: str) -> list[dict[str, Any]]:
    """Follow peel hops forward from `wallet`, then extend backwards.

    Extending backwards matters because a wallet halfway down a chain is just
    as guilty as the one at the top, and looking only forward would score the
    tail of a long chain as clean.
    """
    hops: list[dict[str, Any]] = []
    seen = {wallet}

    current = wallet
    while len(hops) < 30:
        hop = _peel_hop(graph, current)
        if hop is None:
            break
        nxt, txid, share, ts = hop
        if nxt in seen:
            break
        hops.append(
            {"from": current, "to": nxt, "txid": txid,
             "forward_share": round(share, 4), "timestamp": _iso(ts)}
        )
        seen.add(nxt)
        current = nxt

    # Walk upstream: the funder counts as part of the chain if its own split
    # forwarded the bulk to us.
    current = wallet
    while len(hops) < 30:
        funders = _wallet_neighbours(graph, current, "in")
        if len(set(funders)) != 1:
            break
        funder = funders[0]
        if funder in seen:
            break
        hop = _peel_hop(graph, funder)
        if hop is None or hop[0] != current:
            break
        nxt, txid, share, ts = hop
        hops.insert(
            0,
            {"from": funder, "to": current, "txid": txid,
             "forward_share": round(share, 4), "timestamp": _iso(ts)},
        )
        seen.add(funder)
        current = funder

    return hops


def detect_peel_chain(graph: nx.DiGraph, wallet: str) -> dict[str, Any]:
    """Flag a wallet participating in a peel chain.

    Primary test: the wallet sits on a run of at least PEEL_MIN_CHAIN_LENGTH
    consecutive two-way splits, each forwarding 75-98% of its value onward.

    Secondary test: the burst condition from the original spec - in-degree 1
    with more than five distinct outgoing counterparties inside a ten-minute
    window.
    """
    if wallet not in graph:
        return _missing(RULE_PEEL_CHAIN, wallet)

    hops = _walk_peel_chain(graph, wallet)
    if len(hops) >= PEEL_MIN_CHAIN_LENGTH:
        shares = [h["forward_share"] for h in hops]
        lo, hi = min(shares), max(shares)
        # Collapse the range when every hop is the same, so the sentence reads
        # "forwards 90%" rather than "forwards 90%-90%".
        fwd = f"{lo:.0%}" if round(lo, 2) == round(hi, 2) else f"{lo:.0%}-{hi:.0%}"
        peel = (
            f"{1 - hi:.0%}" if round(lo, 2) == round(hi, 2) else f"{1 - hi:.0%}-{1 - lo:.0%}"
        )
        return _result(
            RULE_PEEL_CHAIN,
            True,
            f"Sits on a {len(hops)}-hop peel chain: each transaction forwards "
            f"{fwd} of its value to a fresh wallet while peeling {peel} off to "
            f"the side, a classic layering structure.",
            detector="chain_topology",
            chain_length=len(hops),
            forward_share_range=[min(shares), max(shares)],
            chain=hops[:12],
            chain_wallets=sorted({h["from"] for h in hops} | {h["to"] for h in hops}),
        )

    # Secondary: this wallet is where a slice was peeled *off* to.
    #
    # The side wallets are the point of the whole structure - they are where
    # value actually leaves the chain - but they sit off it rather than on it,
    # so the walk above never reaches them. Without this branch, recall on a
    # planted peel chain caps out near 50%, because roughly half the wallets
    # involved are side wallets.
    funders = set(_wallet_neighbours(graph, wallet, "in"))
    if len(funders) == 1 and not _wallet_neighbours(graph, wallet, "out"):
        funder = next(iter(funders))
        funder_chain = _walk_peel_chain(graph, funder)
        if len(funder_chain) >= PEEL_MIN_CHAIN_LENGTH:
            received = sum(t["amount"] for t in _incoming(graph, wallet))
            forwarded_on = next(
                (h for h in funder_chain if h["from"] == funder), None
            )
            return _result(
                RULE_PEEL_CHAIN,
                True,
                f"Received {received:.4f} BTC peeled off a "
                f"{len(funder_chain)}-hop peel chain and holds it. Side wallets "
                f"like this are where value actually leaves the chain, while "
                f"the bulk keeps moving.",
                detector="peel_recipient",
                funder=funder,
                chain_length=len(funder_chain),
                received_btc=round(received, 8),
                forward_share_at_hop=(
                    forwarded_on["forward_share"] if forwarded_on else None
                ),
                chain_wallets=sorted(
                    {h["from"] for h in funder_chain} | {h["to"] for h in funder_chain}
                ),
            )

    # Tertiary: the burst condition from the original spec.
    in_neighbours = set(_wallet_neighbours(graph, wallet, "in"))
    outgoing = _outgoing(graph, wallet)
    if len(in_neighbours) == 1 and len(outgoing) > 5:
        window = timedelta(minutes=10)
        for i, anchor in enumerate(outgoing):
            burst = [
                t for t in outgoing[i:]
                if t["timestamp"] - anchor["timestamp"] <= window
            ]
            targets = {t["counterparty"] for t in burst}
            if len(targets) > 5:
                return _result(
                    RULE_PEEL_CHAIN,
                    True,
                    f"Funded by a single source, then split to {len(targets)} "
                    f"different wallets within 10 minutes - a rapid dispersal "
                    f"consistent with the head of a peel chain.",
                    detector="burst_window",
                    funder=next(iter(in_neighbours)),
                    targets_in_window=len(targets),
                    window_start=_iso(anchor["timestamp"]),
                    window_minutes=10,
                    sample_targets=sorted(targets)[:10],
                )

    return _result(
        RULE_PEEL_CHAIN,
        False,
        "No peel-chain structure found",
        longest_chain=len(hops),
    )


# --------------------------------------------------------------------------
# 2. Structuring / smurfing
# --------------------------------------------------------------------------

STRUCTURING_WINDOW_HOURS = 24
STRUCTURING_MIN_COUNT = 4
# Payments must sit in the top third of the allowed range to count. Measured
# on the demo dataset, 0.5 admitted ordinary hub traffic and gave 8% precision;
# 0.65 keeps every planted fanout (which pays 70-95% of the limit) while
# excluding wallets that merely move a lot of small amounts.
STRUCTURING_NEAR_RATIO = 0.65


def detect_structuring(
    graph: nx.DiGraph,
    wallet: str,
    threshold: float = 10_000,
    near_ratio: float = STRUCTURING_NEAR_RATIO,
    min_count: int = STRUCTURING_MIN_COUNT,
    check_funder: bool = True,
) -> dict[str, Any]:
    """Flag payments deliberately kept under a reporting threshold.

    `threshold` is in USD; amounts on the graph are BTC and are converted with
    USD_PER_BTC.

    The stated rule - several sub-threshold payments summing to more than the
    threshold in 24 hours - is necessary but nowhere near sufficient on its
    own. Any active wallet moving $500 a dozen times a day satisfies it, and
    on the demo dataset that flags most hub wallets. What actually
    distinguishes structuring is that the amounts *crowd the limit*: someone
    avoiding a $10,000 report sends $9,400, not $500.

    So a payment only counts toward the finding if it falls between
    `near_ratio` x threshold and the threshold itself. This is a deliberate
    tightening of the original rule; the measured effect on false positives is
    recorded in the Phase 4 commit message.
    """
    if wallet not in graph:
        return _missing(RULE_STRUCTURING, wallet)

    outgoing = _outgoing(graph, wallet)
    if not outgoing:
        return _check_structuring_recipient(
            graph, wallet, threshold, near_ratio, min_count, 0, check_funder
        )

    threshold_btc = threshold / USD_PER_BTC
    floor_btc = threshold_btc * near_ratio
    window = timedelta(hours=STRUCTURING_WINDOW_HOURS)

    near = [t for t in outgoing if floor_btc <= t["amount"] < threshold_btc]

    best: dict[str, Any] | None = None
    for i, anchor in enumerate(near):
        batch = [t for t in near[i:] if t["timestamp"] - anchor["timestamp"] <= window]
        total = sum(t["amount"] for t in batch)
        # Distinct recipients matter as much as the count: paying one
        # counterparty four times is an instalment plan, whereas spraying four
        # counterparties is the dispersal that makes structuring worthwhile.
        recipients = {t["counterparty"] for t in batch}
        if (
            len(batch) >= min_count
            and len(recipients) >= min_count
            and total > threshold_btc
        ):
            if best is None or len(batch) > best["count"]:
                best = {
                    "count": len(batch),
                    "total_btc": round(total, 8),
                    "batch": batch,
                    "start": anchor["timestamp"],
                }

    if best is None:
        return _check_structuring_recipient(
            graph, wallet, threshold, near_ratio, min_count, len(near), check_funder
        )

    batch = best["batch"]
    amounts_usd = [t["amount"] * USD_PER_BTC for t in batch]
    span_hours = (
        batch[-1]["timestamp"] - batch[0]["timestamp"]
    ).total_seconds() / 3600
    total_usd = best["total_btc"] * USD_PER_BTC

    lo_usd, hi_usd = min(amounts_usd), max(amounts_usd)
    band = (
        f"${lo_usd:,.0f}"
        if round(lo_usd) == round(hi_usd)
        else f"${lo_usd:,.0f}-${hi_usd:,.0f}"
    )
    return _result(
        RULE_STRUCTURING,
        True,
        f"Sent {best['count']} payments of {band} in "
        f"{span_hours:.1f} hours - each one under the "
        f"${threshold:,.0f} reporting threshold, together ${total_usd:,.0f}. "
        f"Splitting a sum this way is the signature of structuring.",
        threshold_usd=threshold,
        payment_count=best["count"],
        total_usd=round(total_usd, 2),
        total_btc=best["total_btc"],
        largest_single_usd=round(max(amounts_usd), 2),
        span_hours=round(span_hours, 2),
        window_start=_iso(best["start"]),
        recipients=sorted({t["counterparty"] for t in batch})[:15],
        distinct_recipients=len({t["counterparty"] for t in batch}),
    )


def _check_structuring_recipient(
    graph: nx.DiGraph,
    wallet: str,
    threshold: float,
    near_ratio: float,
    min_count: int,
    near_payments: int,
    check_funder: bool = True,
) -> dict[str, Any]:
    """Is this wallet one of the destinations a structuring source paid into?

    The recipients are the other half of the pattern. Individually each one
    looks unremarkable - a single payment in, nothing out - which is precisely
    the design. They are only visible through their funder, and an
    investigator handed the source without its twenty destinations has been
    handed half a case.

    Deliberately narrow: the wallet must be funded by exactly one source, and
    that source must itself be structuring.
    """
    funders = set(_wallet_neighbours(graph, wallet, "in"))
    if check_funder and len(funders) == 1:
        funder = next(iter(funders))
        # check_funder=False stops the funder from looking at *its* funder.
        # Without it, a run of single-funder wallets - which is exactly what a
        # peel chain is - recurses until the interpreter gives up.
        parent = detect_structuring(
            graph, funder, threshold, near_ratio, min_count, check_funder=False
        )
        if parent["triggered"] and parent["evidence"].get("detector") is None:
            received = sum(t["amount"] for t in _incoming(graph, wallet))
            received_usd = received * USD_PER_BTC
            return _result(
                RULE_STRUCTURING,
                True,
                f"Received ${received_usd:,.0f} from a wallet that split "
                f"${parent['evidence']['total_usd']:,.0f} into "
                f"{parent['evidence']['payment_count']} payments, each kept under "
                f"the ${threshold:,.0f} reporting threshold. This wallet is one "
                f"of the destinations that split was aimed at.",
                detector="structuring_recipient",
                role="recipient",
                funder=funder,
                received_usd=round(received_usd, 2),
                received_btc=round(received, 8),
                threshold_usd=threshold,
                source_payment_count=parent["evidence"]["payment_count"],
                source_total_usd=parent["evidence"]["total_usd"],
            )

    return _result(
        RULE_STRUCTURING,
        False,
        "No sub-threshold batching detected",
        near_threshold_payments=near_payments,
    )


# --------------------------------------------------------------------------
# 3. Rapid layering
# --------------------------------------------------------------------------

LAYERING_MIN_DEPTH = 3
LAYERING_MIN_VALUE_RETENTION = 0.5


def detect_rapid_layering(
    graph: nx.DiGraph,
    wallet: str,
    hop_window_minutes: int = 15,
    min_depth: int = LAYERING_MIN_DEPTH,
) -> dict[str, Any]:
    """Flag funds moving through several wallets in quick succession.

    Walks forward from `wallet`, taking only hops that occur after the previous
    one and within `hop_window_minutes` of it. A path reaching `min_depth`
    distinct wallets is layering: money that changes hands three times in a
    quarter of an hour is not paying for anything.

    Each hop must also carry at least LAYERING_MIN_VALUE_RETENTION of the
    previous hop's value. Without that, the walk happily strings together
    unrelated dust payments that merely happen to be close in time, and the
    resulting "chain" is an artefact of the search rather than a movement of
    funds.
    """
    if wallet not in graph:
        return _missing(RULE_RAPID_LAYERING, wallet)

    window = timedelta(minutes=hop_window_minutes)
    best_path: list[dict[str, Any]] = []
    explored = 0

    # Depth-first, keeping the deepest time-and-value-coherent path found.
    stack: list[tuple[str, datetime, float, list[dict[str, Any]], set[str]]] = [
        (wallet, None, None, [], {wallet})
    ]

    while stack and explored < MAX_TRAVERSAL_NODES:
        node, last_ts, last_amt, path, visited = stack.pop()
        explored += 1

        if len(path) > len(best_path):
            best_path = path

        if len(path) >= 8:
            continue

        candidates = _outgoing(graph, node)
        if last_ts is not None:
            candidates = [
                t for t in candidates
                if last_ts < t["timestamp"] <= last_ts + window
                and t["amount"] >= last_amt * LAYERING_MIN_VALUE_RETENTION
            ]
        candidates = [t for t in candidates if t["counterparty"] not in visited]
        # Follow the largest movements first; that is where the money went.
        candidates.sort(key=lambda t: t["amount"], reverse=True)

        for t in candidates[:MAX_BRANCHING]:
            stack.append(
                (
                    t["counterparty"],
                    t["timestamp"],
                    t["amount"],
                    [*path, {
                        "from": node, "to": t["counterparty"], "txid": t["txid"],
                        "amount": round(t["amount"], 8),
                        "timestamp": _iso(t["timestamp"]),
                    }],
                    visited | {t["counterparty"]},
                )
            )

    if len(best_path) < min_depth:
        return _result(
            RULE_RAPID_LAYERING,
            False,
            "No rapid multi-hop movement detected",
            longest_path=len(best_path),
        )

    hops = best_path
    start = datetime.fromisoformat(hops[0]["timestamp"])
    end = datetime.fromisoformat(hops[-1]["timestamp"])
    elapsed = (end - start).total_seconds() / 60
    chain_wallets = [hops[0]["from"]] + [h["to"] for h in hops]

    return _result(
        RULE_RAPID_LAYERING,
        True,
        f"Funds moved through {len(chain_wallets) - 1} further wallets in "
        f"{elapsed:.0f} minutes, each hop within {hop_window_minutes} minutes of "
        f"the last. Rapid onward movement like this obscures the trail rather "
        f"than settling any payment.",
        depth=len(hops),
        elapsed_minutes=round(elapsed, 1),
        hop_window_minutes=hop_window_minutes,
        path=chain_wallets[:12],
        hops=hops[:12],
        value_start_btc=hops[0]["amount"],
        value_end_btc=hops[-1]["amount"],
    )


# --------------------------------------------------------------------------
# 4. Round trip / wash
# --------------------------------------------------------------------------

ROUND_TRIP_MAX_HOPS = 6
# A loop must pass through at least two intermediaries. Two hops is a -> b -> a,
# which is a refund or change coming back, not a wash: on the demo dataset every
# genuine wash loop found was 3 hops and every false positive was 2, with no
# overlap.
ROUND_TRIP_MIN_HOPS = 3
# Two days, not two weeks. Over a fortnight, ordinary traffic in a graph this
# dense throws up value-coherent cycles by chance: every false positive an
# earlier 14-day horizon produced spanned 90-330 hours, while the planted wash
# loops complete in under 40 minutes. Widen it only with evidence that real
# loops in the data take longer.
ROUND_TRIP_MAX_DAYS = 2
# Small headroom above 1.0 absorbs float noise in the proportional split,
# without admitting a hop that genuinely gains value.
ROUND_TRIP_VALUE_TOLERANCE = 1.001


def _amounts_similar(a: float, b: float, threshold: float) -> bool:
    """Are two amounts close enough to be the same money?

    Symmetric on purpose. Testing only `b >= a * threshold` lets a much
    *larger* unrelated payment satisfy the rule: an early version of this
    detector reported a clean wallet as a round trip because 0.004 BTC went
    out and an unconnected 0.019 BTC came back, which it happily called a
    475% return. Comparing the smaller against the larger bounds it on both
    sides and caps the ratio at 1.
    """
    if a <= 0 or b <= 0:
        return False
    return min(a, b) / max(a, b) >= threshold


def detect_round_trip(
    graph: nx.DiGraph,
    wallet: str,
    similarity_threshold: float = 0.85,
    max_hops: int = ROUND_TRIP_MAX_HOPS,
    max_days: int = ROUND_TRIP_MAX_DAYS,
) -> dict[str, Any]:
    """Flag value that leaves a wallet and comes back largely intact.

    Searches forward for a time-ordered path that returns to `wallet` while
    retaining at least `similarity_threshold` of the amount that originally
    left. Money that travels in a circle and arrives back nearly whole has not
    bought anything; it has been laundered through intermediaries, or washed to
    manufacture the appearance of volume.
    """
    if wallet not in graph:
        return _missing(RULE_ROUND_TRIP, wallet)

    horizon = timedelta(days=max_days)
    explored = 0

    for seed in _outgoing(graph, wallet):
        origin_amount = seed["amount"]
        if origin_amount <= 0:
            continue

        stack: list[tuple[str, datetime, float, list[dict[str, Any]], set[str]]] = [
            (
                seed["counterparty"],
                seed["timestamp"],
                seed["amount"],
                [{"from": wallet, "to": seed["counterparty"], "txid": seed["txid"],
                  "amount": round(seed["amount"], 8),
                  "timestamp": _iso(seed["timestamp"])}],
                {wallet, seed["counterparty"]},
            )
        ]

        while stack and explored < MAX_TRAVERSAL_NODES:
            node, ts, amount, path, visited = stack.pop()
            explored += 1
            if len(path) >= max_hops:
                continue

            # Filter before truncating. Slicing a timestamp-ordered list to
            # MAX_BRANCHING first would silently drop the returning leg on a
            # hub wallet with hundreds of out-edges, which is exactly the case
            # that matters.
            candidates = [
                t for t in _outgoing(graph, node)
                if ts < t["timestamp"] <= seed["timestamp"] + horizon
                and _amounts_similar(origin_amount, t["amount"], similarity_threshold)
                # Value in a real cycle only decays, through fees and the
                # mixer's cut. Allowing it to rise lets the search stitch
                # together unrelated payments that merely pass near each other.
                and t["amount"] <= amount * ROUND_TRIP_VALUE_TOLERANCE
            ]
            candidates.sort(key=lambda t: abs(t["amount"] - amount))

            for t in candidates[:MAX_BRANCHING]:
                step = {
                    "from": node, "to": t["counterparty"], "txid": t["txid"],
                    "amount": round(t["amount"], 8),
                    "timestamp": _iso(t["timestamp"]),
                }

                if t["counterparty"] == wallet:
                    if len(path) + 1 < ROUND_TRIP_MIN_HOPS:
                        continue  # a -> b -> a is a refund, not a circuit
                    returned = t["amount"]
                    elapsed = (
                        t["timestamp"] - seed["timestamp"]
                    ).total_seconds() / 3600
                    full_path = [*path, step]
                    ring = [wallet] + [h["to"] for h in full_path]
                    retained = returned / origin_amount
                    # Never round a partial return up to a flat "100%" - the
                    # small shortfall is the fee trail, and an analyst reading
                    # "100%" would reasonably assume it was exact.
                    retained_str = (
                        f"{retained:.1%}" if 0.995 <= retained < 1 else f"{retained:.0%}"
                    )
                    return _result(
                        RULE_ROUND_TRIP,
                        True,
                        f"{origin_amount:.4f} BTC left this wallet and "
                        f"{returned:.4f} BTC "
                        f"({retained_str} of it) returned "
                        f"{elapsed:.1f} hours later after passing through "
                        f"{len(full_path) - 1} intermediary "
                        f"wallet{'s' if len(full_path) != 2 else ''}. Funds that "
                        f"travel in a circle and come back intact have been "
                        f"cycled, not spent.",
                        hops=len(full_path),
                        sent_btc=round(origin_amount, 8),
                        returned_btc=round(returned, 8),
                        retained_fraction=round(returned / origin_amount, 4),
                        elapsed_hours=round(elapsed, 2),
                        similarity_threshold=similarity_threshold,
                        ring=ring,
                        path=full_path,
                    )

                if t["counterparty"] not in visited:
                    stack.append(
                        (
                            t["counterparty"], t["timestamp"], t["amount"],
                            [*path, step], visited | {t["counterparty"]},
                        )
                    )

    # The wallet is not the origin of a loop - but it may sit on one.
    #
    # In a ring a -> b -> c -> a, only `a` sees its own money come back. `b`
    # and `c` are conduits: the value passes through and closes behind them.
    # Classifying them as non-originators is correct, but an investigator
    # needs the whole ring, not one wallet of it, so they are reported here
    # with their actual role rather than left unflagged.
    loop = _find_loop_through(graph, wallet, similarity_threshold, max_hops, horizon)
    if loop is not None:
        ring, elapsed_hours, amount = loop
        return _result(
            RULE_ROUND_TRIP,
            True,
            f"Sits on a {len(ring) - 1}-wallet laundering loop that closes back "
            f"through this wallet within {elapsed_hours:.1f} hours, carrying "
            f"{amount:.4f} BTC. Funds passed through here on their way around "
            f"the circuit.",
            detector="cycle_participant",
            role="intermediary",
            hops=len(ring) - 1,
            elapsed_hours=round(elapsed_hours, 2),
            amount_btc=round(amount, 8),
            retained_fraction=1.0,
            similarity_threshold=similarity_threshold,
            ring=ring,
        )

    return _result(
        RULE_ROUND_TRIP,
        False,
        "No returning value flow detected",
        similarity_threshold=similarity_threshold,
    )


def _find_loop_through(
    graph: nx.DiGraph,
    wallet: str,
    similarity_threshold: float,
    max_hops: int,
    horizon: timedelta,
) -> tuple[list[str], float, float] | None:
    """Find a value-coherent loop that passes *through* `wallet`.

    Walks forward from `wallet` under the same time and value constraints as
    the origin search, then checks whether anything reached is also a wallet
    that funded `wallet` shortly beforehand. That closes a circuit containing
    `wallet` without requiring the money to return to it.
    """
    incoming = _incoming(graph, wallet)
    if not incoming:
        return None
    funders = {t["counterparty"]: t for t in incoming}

    explored = 0
    stack: list[tuple[str, datetime, float, list[str]]] = []
    for seed in _outgoing(graph, wallet):
        stack.append(
            (seed["counterparty"], seed["timestamp"], seed["amount"], [wallet, seed["counterparty"]])
        )

    while stack and explored < MAX_TRAVERSAL_NODES:
        node, ts, amount, path = stack.pop()
        explored += 1

        funding = funders.get(node)
        if funding is not None and funding["timestamp"] < ts:
            if _amounts_similar(funding["amount"], amount, similarity_threshold):
                elapsed = (ts - funding["timestamp"]).total_seconds() / 3600
                if (
                    elapsed <= horizon.total_seconds() / 3600
                    and len(path) >= ROUND_TRIP_MIN_HOPS
                ):
                    return [*path, wallet], elapsed, amount

        if len(path) > max_hops:
            continue

        candidates = [
            t for t in _outgoing(graph, node)
            if ts < t["timestamp"] <= ts + horizon
            and t["amount"] <= amount * ROUND_TRIP_VALUE_TOLERANCE
            and _amounts_similar(amount, t["amount"], similarity_threshold)
            and t["counterparty"] not in path
        ]
        candidates.sort(key=lambda t: abs(t["amount"] - amount))
        for t in candidates[:MAX_BRANCHING]:
            stack.append(
                (t["counterparty"], t["timestamp"], t["amount"], [*path, t["counterparty"]])
            )

    return None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_all_rules(graph: nx.DiGraph, wallet: str) -> list[dict[str, Any]]:
    """Run every detector on `wallet` and return only the ones that fired."""
    results = [
        detect_peel_chain(graph, wallet),
        detect_structuring(graph, wallet),
        detect_rapid_layering(graph, wallet),
        detect_round_trip(graph, wallet),
    ]
    return [r for r in results if r["triggered"]]


def rule_vector(graph: nx.DiGraph, wallet: str) -> dict[str, int]:
    """One-hot encoding of triggered rules, for the Phase 5 feature vector."""
    fired = {r["rule"] for r in run_all_rules(graph, wallet)}
    return {f"rule_{name}": int(name in fired) for name in ALL_RULES}
