"""Synthetic Bitcoin transaction generator.

Produces a realistic-looking stream of "clean" transactions, then injects known
laundering patterns at random points in the timeline so we can measure whether
the detection pipeline actually finds them.

Everything emitted conforms to the `Transaction` schema in app/models.py.

Run it directly to regenerate the demo dataset:

    python -m app.services.data_generator

Amounts are denominated in BTC. Reporting thresholds in anti-money-laundering
work are fiat, though, so `USD_PER_BTC` below is the conversion the structuring
detector in domain_rules.py uses to compare against a $10,000 threshold.
"""

from __future__ import annotations

import json
import random
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

from app.models import Transaction

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# Nominal rate used to express BTC amounts against fiat reporting thresholds.
# Not meant to be accurate - it just has to be consistent across the project.
USD_PER_BTC = 60_000.0

# Base58 omits 0, O, I and l, because they are too easy to confuse visually.
BASE58_ALPHABET = "".join(
    c for c in string.digits + string.ascii_letters if c not in "0OIl"
)

# Legacy P2PKH addresses start with 1, P2SH with 3.
ADDRESS_PREFIXES = ("1", "3")

SCRIPT_TYPES = ["P2PKH", "P2SH", "P2WPKH", "P2WSH", "P2TR"]
SCRIPT_TYPE_WEIGHTS = [0.30, 0.20, 0.35, 0.05, 0.10]

# (ISO country code, ASN) pairs. Real ASNs, for plausible-looking metadata.
GEO_POOL = [
    ("IN", "AS9498 BHARTI-AIRTEL"),
    ("IN", "AS55836 RELIANCE-JIO"),
    ("US", "AS7922 COMCAST-7922"),
    ("US", "AS16509 AMAZON-02"),
    ("DE", "AS3320 DTAG"),
    ("NL", "AS60781 LEASEWEB-NL"),
    ("RU", "AS12389 ROSTELECOM"),
    ("CN", "AS4134 CHINANET-BACKBONE"),
    ("SG", "AS9506 SINGTEL-FIBRE"),
    ("GB", "AS2856 BT-UK-AS"),
    ("BR", "AS27699 TELEFONICA-BR"),
    ("NG", "AS37027 MTN-NIGERIA"),
]

# Jurisdictions that tend to show up disproportionately in laundering traffic.
# Injected patterns bias toward these, so geo becomes a weakly useful signal.
HIGH_RISK_GEO = [g for g in GEO_POOL if g[0] in {"RU", "CN", "NL", "NG"}]

BITCOIN_P2P_PORTS = [8333, 8333, 8333, 8333, 18333]

# Anchor the whole dataset to one moment so reruns with the same seed match.
NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_DAYS = 30

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def _wallet_address(rng: random.Random) -> str:
    """A base58 string shaped like a legacy Bitcoin address (34 chars)."""
    prefix = rng.choice(ADDRESS_PREFIXES)
    body = "".join(rng.choice(BASE58_ALPHABET) for _ in range(33))
    return prefix + body


def _txid(rng: random.Random) -> str:
    """A 64-character hex string, the shape of a real transaction hash."""
    return "".join(rng.choice("0123456789abcdef") for _ in range(64))


def _normal_amount(rng: random.Random) -> float:
    """Log-normal BTC amount: mostly small, occasionally large.

    mu = ln(0.05) puts the median near 0.05 BTC; sigma = 1.4 gives a long right
    tail, so a handful of transactions land in the tens of BTC.
    """
    return round(min(rng.lognormvariate(-3.0, 1.4), 250.0), 8)


def _fee_for(amount: float, rng: random.Random) -> float:
    """A plausible miner fee, floored so it never rounds to zero."""
    return round(max(0.00001, amount * rng.uniform(0.0002, 0.003)), 8)


def _make_ip_pool(size: int, faker: Faker) -> list[str]:
    """A fixed set of source IPs that transactions are broadcast from.

    Drawing a fresh random IP per transaction would leave every IP node in the
    graph at degree 1, which makes the whole network layer useless: the reason
    IPs are in the graph at all is so an analyst can pivot from a wallet to the
    host that broadcast it and on to the *other* wallets sharing that host.
    Reuse is what creates that link.
    """
    return [faker.ipv4_public() for _ in range(size)]


def _timestamp_in_window(rng: random.Random) -> datetime:
    """A random moment in the last WINDOW_DAYS, biased toward business hours."""
    offset = rng.uniform(0, WINDOW_DAYS * 24 * 3600)
    ts = NOW - timedelta(seconds=offset)
    # Nudge a portion of traffic into 08:00-20:00 UTC so the hourly histogram
    # is not perfectly flat, which would look obviously synthetic.
    if rng.random() < 0.55 and not 8 <= ts.hour < 20:
        ts = ts.replace(hour=rng.randint(8, 19))
    return ts


def _build_transaction(
    inputs: list[tuple[str, float]],
    outputs: list[tuple[str, float]],
    timestamp: datetime,
    rng: random.Random,
    faker: Faker,
    high_risk_geo: bool = False,
    src_ip: str | None = None,
    ip_pool: list[str] | None = None,
) -> Transaction:
    """Assemble a Transaction, deriving the fee from the input/output gap.

    Real transactions satisfy `sum(inputs) == sum(outputs) + fee`, and several
    downstream features (amount variance, fan-in/fan-out value ratios) only
    make sense if that identity holds, so callers must leave room for the fee
    in their outputs.

    `src_ip` pins the broadcasting host, which injected patterns use to model
    one actor operating from one machine. Otherwise IPs are drawn from
    `ip_pool` so hosts recur across the dataset.
    """
    total_in = sum(a for _, a in inputs)
    total_out = sum(a for _, a in outputs)
    fee = round(total_in - total_out, 8)
    if fee <= 0:
        raise ValueError(
            f"outputs ({total_out}) must be less than inputs ({total_in}) to leave a fee"
        )

    geo_country, asn = rng.choice(HIGH_RISK_GEO if high_risk_geo else GEO_POOL)
    pool = ip_pool or [faker.ipv4_public() for _ in range(2)]

    return Transaction(
        txid=_txid(rng),
        timestamp=timestamp,
        src_ip=src_ip or rng.choice(pool),
        dst_ip=rng.choice(pool),
        src_port=rng.randint(32768, 60999),
        dst_port=rng.choice(BITCOIN_P2P_PORTS),
        input_addresses=[a for a, _ in inputs],
        output_addresses=[a for a, _ in outputs],
        input_amounts=[amt for _, amt in inputs],
        output_amounts=[amt for _, amt in outputs],
        fee=fee,
        script_type=rng.choices(SCRIPT_TYPES, weights=SCRIPT_TYPE_WEIGHTS)[0],
        geo_country=geo_country,
        asn=asn,
    )


# --------------------------------------------------------------------------
# Normal traffic
# --------------------------------------------------------------------------


def generate_normal_transactions(
    n: int,
    rng: random.Random | None = None,
    faker: Faker | None = None,
    wallet_pool_size: int | None = None,
    ip_pool: list[str] | None = None,
) -> list[Transaction]:
    """Generate `n` clean transactions spread over the last 30 days.

    Wallets are drawn from a fixed pool rather than invented per transaction;
    otherwise every node would have degree 1 and the graph would carry no
    structure for the centrality features to measure. Selection is
    preferential - wallets already in use are more likely to be reused - which
    produces the heavy-tailed degree distribution real transaction graphs have.
    """
    rng = rng or random.Random(1337)
    faker = faker or Faker()

    pool_size = wallet_pool_size or max(50, n // 4)
    pool = [_wallet_address(rng) for _ in range(pool_size)]
    # Roughly one host per dozen transactions, so IPs recur and the network
    # layer of the graph carries structure worth pivoting through.
    ip_pool = ip_pool or _make_ip_pool(max(20, n // 12), faker)
    # Weight index 0 highest so a few wallets act as hubs (exchanges, mixers).
    weights = [1.0 / (i + 1) ** 0.75 for i in range(pool_size)]

    transactions: list[Transaction] = []
    for _ in range(n):
        n_in = rng.choices([1, 2, 3], weights=[0.72, 0.20, 0.08])[0]
        n_out = rng.choices([1, 2, 3], weights=[0.40, 0.48, 0.12])[0]

        in_addrs = rng.choices(pool, weights=weights, k=n_in)
        out_addrs = rng.choices(pool, weights=weights, k=n_out)

        inputs = [(a, _normal_amount(rng)) for a in in_addrs]
        total_in = sum(a for _, a in inputs)
        fee = _fee_for(total_in, rng)
        spendable = total_in - fee

        # Split the spendable amount across outputs via random proportions.
        cuts = sorted(rng.random() for _ in range(n_out - 1))
        bounds = [0.0, *cuts, 1.0]
        shares = [bounds[i + 1] - bounds[i] for i in range(n_out)]
        out_amounts = [round(spendable * s, 8) for s in shares]
        # Push rounding drift into the largest output so the identity holds.
        drift = round(spendable - sum(out_amounts), 8)
        out_amounts[out_amounts.index(max(out_amounts))] = round(
            out_amounts[out_amounts.index(max(out_amounts))] + drift, 8
        )

        outputs = list(zip(out_addrs, out_amounts))
        if any(a <= 0 for _, a in outputs):
            continue  # degenerate split; skip rather than emit a zero output

        transactions.append(
            _build_transaction(
                inputs,
                outputs,
                _timestamp_in_window(rng),
                rng,
                faker,
                ip_pool=ip_pool,
            )
        )

    return transactions


# --------------------------------------------------------------------------
# Injected laundering patterns
# --------------------------------------------------------------------------


def inject_peel_chain(
    start_wallet: str,
    hops: int,
    start_amount: float,
    start_time: datetime,
    rng: random.Random | None = None,
    faker: Faker | None = None,
    actor_ip: str | None = None,
    ip_pool: list[str] | None = None,
) -> list[Transaction]:
    """Simulate a peel chain.

    A wallet holding a large balance repeatedly "peels" a small slice off to a
    side wallet - which is where the value actually exits - while forwarding
    the bulk to a fresh wallet the same actor controls. Each hop is a single
    transaction with one input and two outputs, a few minutes apart.
    """
    rng = rng or random.Random()
    faker = faker or Faker()

    transactions: list[Transaction] = []
    current_wallet = start_wallet
    current_amount = start_amount
    ts = start_time

    for _ in range(hops):
        fee = _fee_for(current_amount, rng)
        spendable = current_amount - fee
        if spendable <= 0:
            break

        peel_ratio = rng.uniform(0.06, 0.14)  # roughly 10% peeled off each hop
        peel_amount = round(spendable * peel_ratio, 8)
        forward_amount = round(spendable - peel_amount, 8)
        if peel_amount <= 0 or forward_amount <= 0:
            break

        next_wallet = _wallet_address(rng)
        side_wallet = _wallet_address(rng)

        transactions.append(
            _build_transaction(
                inputs=[(current_wallet, current_amount)],
                outputs=[(next_wallet, forward_amount), (side_wallet, peel_amount)],
                timestamp=ts,
                rng=rng,
                faker=faker,
                high_risk_geo=True,
                src_ip=actor_ip,
                ip_pool=ip_pool,
            )
        )

        current_wallet = next_wallet
        current_amount = forward_amount
        ts += timedelta(minutes=rng.uniform(2, 9))

    return transactions


def inject_rapid_fanout(
    source_wallet: str,
    num_targets: int,
    start_time: datetime,
    rng: random.Random | None = None,
    faker: Faker | None = None,
    actor_ip: str | None = None,
    ip_pool: list[str] | None = None,
) -> list[Transaction]:
    """Simulate structuring / smurfing.

    One wallet pushes many small payments to many distinct wallets inside a
    short window. Each payment is sized to sit just under the $10,000 reporting
    threshold, which is the whole point of the technique: individually
    unremarkable, collectively far above the limit.
    """
    rng = rng or random.Random()
    faker = faker or Faker()

    threshold_btc = 10_000.0 / USD_PER_BTC
    transactions: list[Transaction] = []
    ts = start_time

    for _ in range(num_targets):
        # 70-95% of the threshold: deliberately close to it, never over.
        amount_out = round(threshold_btc * rng.uniform(0.70, 0.95), 8)
        fee = _fee_for(amount_out, rng)
        target = _wallet_address(rng)

        transactions.append(
            _build_transaction(
                inputs=[(source_wallet, round(amount_out + fee, 8))],
                outputs=[(target, amount_out)],
                timestamp=ts,
                rng=rng,
                faker=faker,
                high_risk_geo=True,
                src_ip=actor_ip,
                ip_pool=ip_pool,
            )
        )
        ts += timedelta(seconds=rng.uniform(20, 150))

    return transactions


def inject_round_trip(
    wallets: list[str],
    amount: float,
    start_time: datetime,
    rng: random.Random | None = None,
    faker: Faker | None = None,
    actor_ip: str | None = None,
    ip_pool: list[str] | None = None,
) -> list[Transaction]:
    """Simulate a mixing / wash loop.

    Funds hop through a ring of wallets and come back close to where they
    started. Only fees and a small mixer cut are lost at each hop, so the
    returning amount stays recognisable as the same money.
    """
    rng = rng or random.Random()
    faker = faker or Faker()

    if len(wallets) < 3:
        raise ValueError("a round trip needs at least 3 wallets to form a loop")

    transactions: list[Transaction] = []
    ring = [*wallets, wallets[0]]  # close the loop back to the origin
    current_amount = amount
    ts = start_time

    for src, dst in zip(ring, ring[1:]):
        fee = _fee_for(current_amount, rng)
        # Skim a little at each hop, as a real mixer would take a cut.
        skim = round(current_amount * rng.uniform(0.0, 0.02), 8)
        forward = round(current_amount - fee - skim, 8)
        if forward <= 0:
            break

        outputs = [(dst, forward)]
        if skim > 0:
            outputs.append((_wallet_address(rng), skim))

        transactions.append(
            _build_transaction(
                inputs=[(src, current_amount)],
                outputs=outputs,
                timestamp=ts,
                rng=rng,
                faker=faker,
                high_risk_geo=True,
                src_ip=actor_ip,
                ip_pool=ip_pool,
            )
        )
        current_amount = forward
        ts += timedelta(minutes=rng.uniform(3, 20))

    return transactions


# --------------------------------------------------------------------------
# Dataset assembly
# --------------------------------------------------------------------------


def build_dataset(
    n_normal: int = 5000,
    n_peel_chains: int = 3,
    n_fanouts: int = 2,
    n_round_trips: int = 2,
    seed: int = 1337,
) -> tuple[list[Transaction], dict]:
    """Generate the full demo dataset plus its ground truth.

    Returns (transactions sorted by timestamp, ground_truth dict).
    """
    rng = random.Random(seed)
    faker = Faker()
    Faker.seed(seed)

    ip_pool = _make_ip_pool(max(20, n_normal // 12), faker)
    transactions = generate_normal_transactions(
        n_normal, rng=rng, faker=faker, ip_pool=ip_pool
    )
    clean_wallets = sorted(
        {a for t in transactions for a in t.input_addresses + t.output_addresses}
    )

    # Each injected pattern broadcasts from a single host it does not share
    # with anyone else. That is what makes "these thirty wallets all came from
    # one IP" a finding rather than a coincidence, and it gives the graph an
    # IP node worth pivoting through during the demo.
    actor_ips = _make_ip_pool(n_peel_chains + n_fanouts + n_round_trips, faker)

    patterns: list[dict] = []

    def _record(pattern_type: str, txs: list[Transaction], **details) -> None:
        wallets = sorted(
            {a for t in txs for a in t.input_addresses + t.output_addresses}
        )
        patterns.append(
            {
                "pattern_type": pattern_type,
                "wallets": wallets,
                "txids": [t.txid for t in txs],
                "transaction_count": len(txs),
                "start_time": min(t.timestamp for t in txs).isoformat(),
                "end_time": max(t.timestamp for t in txs).isoformat(),
                "actor_ips": sorted({t.src_ip for t in txs}),
            "details": details,
            }
        )
        transactions.extend(txs)

    # Peel chains are seeded from a wallet that already exists in normal
    # traffic, so the pattern emerges out of legitimate-looking activity
    # rather than appearing as an isolated island a detector could spot
    # trivially.
    for _ in range(n_peel_chains):
        origin = rng.choice(clean_wallets)
        hops = rng.randint(6, 10)
        amount = round(rng.uniform(8.0, 40.0), 8)
        txs = inject_peel_chain(
            origin,
            hops,
            amount,
            _timestamp_in_window(rng),
            rng=rng,
            faker=faker,
            actor_ip=actor_ips.pop(),
            ip_pool=ip_pool,
        )
        _record(
            "peel_chain", txs, origin_wallet=origin, hops=len(txs), start_amount=amount
        )

    for _ in range(n_fanouts):
        source = rng.choice(clean_wallets)
        num_targets = rng.randint(12, 25)
        txs = inject_rapid_fanout(
            source,
            num_targets,
            _timestamp_in_window(rng),
            rng=rng,
            faker=faker,
            actor_ip=actor_ips.pop(),
            ip_pool=ip_pool,
        )
        total_btc = round(sum(sum(t.output_amounts) for t in txs), 8)
        _record(
            "rapid_fanout",
            txs,
            source_wallet=source,
            num_targets=num_targets,
            total_btc=total_btc,
            total_usd=round(total_btc * USD_PER_BTC, 2),
        )

    for _ in range(n_round_trips):
        ring_size = rng.randint(3, 5)
        ring = [rng.choice(clean_wallets)] + [
            _wallet_address(rng) for _ in range(ring_size - 1)
        ]
        amount = round(rng.uniform(2.0, 15.0), 8)
        txs = inject_round_trip(
            ring,
            amount,
            _timestamp_in_window(rng),
            rng=rng,
            faker=faker,
            actor_ip=actor_ips.pop(),
            ip_pool=ip_pool,
        )
        _record("round_trip", txs, ring=ring, start_amount=amount)

    transactions.sort(key=lambda t: t.timestamp)

    # A wallet can belong to more than one pattern (an origin wallet reused by
    # chance), so map each guilty wallet to every pattern it appears in.
    guilty: dict[str, list[str]] = {}
    for p in patterns:
        for w in p["wallets"]:
            guilty.setdefault(w, [])
            if p["pattern_type"] not in guilty[w]:
                guilty[w].append(p["pattern_type"])

    ground_truth = {
        "generated_at": NOW.isoformat(),
        "seed": seed,
        "total_transactions": len(transactions),
        "normal_transactions": n_normal,
        "guilty_wallet_count": len(guilty),
        "guilty_wallets": dict(sorted(guilty.items())),
        "patterns": patterns,
    }
    return transactions, ground_truth


def main() -> None:
    transactions, ground_truth = build_dataset()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tx_path = DATA_DIR / "synthetic_transactions.json"
    gt_path = DATA_DIR / "ground_truth.json"

    tx_path.write_text(
        json.dumps([json.loads(t.model_dump_json()) for t in transactions], indent=2),
        encoding="utf-8",
    )
    gt_path.write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

    all_wallets = {
        a for t in transactions for a in t.input_addresses + t.output_addresses
    }
    guilty = ground_truth["guilty_wallets"]

    print("=" * 70)
    print("SYNTHETIC DATASET GENERATED")
    print("=" * 70)
    print(f"  Transactions written : {len(transactions):,}")
    print(f"  Distinct wallets     : {len(all_wallets):,}")
    print(
        f"  Guilty wallets       : {len(guilty):,} "
        f"({len(guilty) / len(all_wallets):.1%} of all wallets)"
    )
    print(
        f"  Time span            : {transactions[0].timestamp:%Y-%m-%d} "
        f"to {transactions[-1].timestamp:%Y-%m-%d}"
    )
    print()
    print("  Injected patterns:")
    for p in ground_truth["patterns"]:
        print(
            f"    - {p['pattern_type']:<13} {p['transaction_count']:>3} txs, "
            f"{len(p['wallets']):>3} wallets, starting {p['start_time'][:16]}"
        )
    print()
    print("  Guilty wallet addresses:")
    for wallet, types in guilty.items():
        print(f"    {wallet}  [{', '.join(types)}]")
    print()
    print(f"  -> {tx_path}")
    print(f"  -> {gt_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
