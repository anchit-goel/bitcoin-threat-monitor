# Bitcoin Transaction Threat Monitor — Progress So Far

**SIH 2026 internal hackathon.** Status as of 2026-09-04.

Repo: https://github.com/Harit117/bitcoin-threat-monitor
Live demo: https://harit117.github.io/bitcoin-threat-monitor/ (static snapshot — see caveat below)

---

## 1. What's built (Phases 0–8, all done)

A full offline pipeline, end to end: upload a transaction file → parse it →
build a wallet/IP graph → score every wallet with ML + hand-written rules →
explain each score in plain English → serve it to a React dashboard.

| Phase | What | Status |
|---|---|---|
| 0 | Project scaffold, Pydantic schema contract | done |
| 1 | Synthetic transaction generator (peel chains, fanouts, round trips planted) | done |
| 2 | Ingestion (JSON/CSV/XML) + wallet/IP graph builder | done |
| 3 | ML baseline on Elliptic's own 166-feature space | done |
| 4 | Domain rule detectors (4 laundering patterns) | done |
| 5 | Graph features + wallet-space scoring model + SHAP explanations | done |
| 6 | FastAPI backend, 6 endpoints | done |
| 7–8 | React frontend: link-analysis graph + dashboard | done |
| 9 | Full integration pass | **not started** |

**Stack:** FastAPI 0.141 / Pydantic 2.13 / networkx / scikit-learn / SHAP on the
backend; React 19 / Vite / Tailwind v4 / react-force-graph on the frontend.
4,897 lines of backend Python across 13 modules. 78 backend tests, all passing.

### The model that actually scores wallets

- **RandomForestClassifier** (300 trees, `class_weight="balanced"`) — supervised,
  trained on our synthetic ground truth
- **IsolationForest** (300 trees, contamination 0.0865) — unsupervised, fitted on
  clean wallets only, as a hedge for behaviour the forest has never seen
- Combined `risk_score = 0.75 × RF_probability + 0.25 × normalised_anomaly`
- 14 input features: 10 named graph measures (degree, PageRank, clustering,
  velocity, amounts, variance) + 4 binary rule flags
- Explanations: SHAP `TreeExplainer`, with rule findings shown first since
  they're already evidence, not attribution
- Held-out performance (separate random seed, never seen in training):
  precision 0.911, recall 0.968, F1 0.939

**Notable finding from building this:** refitting without the 4 rule flags
gives the *identical* score. The graph features already carry all the signal;
the rules earn their place as evidence and explanation, not as model input.

### Domain rule detectors (4, hand-tuned against ground truth)

Peel chain, structuring/smurfing, rapid layering, round-trip/wash. The rules
as originally specified scored **9% precision with a 27.6% false-positive
rate** — useless. Tuned against measured ground truth (e.g. requiring a
forwarded-to wallet be "freshly funded" — exactly one funder — cut 321 false
positives on its own), they now hit **96% precision, 93% recall, F1 94%** on
the synthetic dataset.

### Performance work

Two real bugs found by profiling, not guessing:
- A full ingest (5,078 tx → 1,352 wallets scored) was 12s → now **3.5s**,
  after finding that a cache's own signature check (`graph.number_of_edges()`)
  is O(V) in networkx, not O(1) — it was costing 100M generator steps
- The `/alerts` endpoint hung on a full page request because it explained
  wallets one at a time (~1,200 separate SHAP calls); batched into one pass,
  it now returns in 0.73s

### Frontend bug found and fixed (2026-09-03)

A friend reported the graph nodes were completely unclickable and undraggable
on the published demo. Reproduced and measured: the click hit-area was sized
in *graph* units, so at the default zoom a wallet's clickable radius was
**1.13 CSS pixels** — smaller than a cursor. Fixed by flooring the hit radius
at 9 CSS pixels in screen space. Verified with an 882-point click sweep:
**0/882 → 33/882** hits, on the live deployed site, before/after.

---

## 2. The real-data finding (2026-09-03) — the most important result so far

Every accuracy number above (96–99%) was measured against patterns **our own
generator planted**. That's circular — it answers "do we detect what we drew
ourselves?", not "does this work on real crime."

So we benchmarked our structural features against the **real Elliptic
dataset**: 203,769 real Bitcoin transactions, 4,545 labelled illicit by
Elliptic's own analysts (verified byte-exact against the published paper
figures). Split by connected component (= the dataset's 49 time steps) so no
edge crosses train/test.

**Result: our features are close to useless on real data.**

| Measure | Result |
|---|---|
| ROC AUC (random forest) | 0.685 |
| Every feature individually | 0.387–0.498 (0.5 = coin flip) |
| Precision@50 (top of an analyst's queue) | **8.0% — worse than random** |
| Isolation forest ROC AUC | **0.358 — worse than chance** |

Illicit transactions turn out to be *less* connected than licit ones on real
data, which is why an anomaly detector fitted on licit behaviour actively
misranks them.

**What this does and doesn't mean:** it only tests the structural half of the
feature set (degree, PageRank, clustering — everything computable from an
edge list alone). Amount and timing features (velocity, totals, variance)
couldn't be tested because Elliptic's edge list carries no values, and the
file that does (658 MB) truncates on every mirror reachable here. Those
untested features are plausibly where the real signal is.

**The honest framing for the write-up and Q&A:** quote the synthetic 96–99%
as *"detects the laundering patterns we modelled,"* never as real-world
accuracy. The README now leads with this warning rather than the flattering
number.

---

## 3. Real datasets now in hand

| Dataset | What it is | Status |
|---|---|---|
| **Elliptic** (graph + labels) | 203,769 real transactions, 4,545 illicit | Downloaded, verified, used for the benchmark above |
| **Elliptic features file** (166 cols) | The part Elliptic never disclosed the meaning of | Unreachable — 658 MB, truncates on every mirror tried |
| **BitcoinHeist** | 2,916,697 real addresses, 41,413 labelled to 28 real ransomware families (Cerber, Locky, WannaCry, CryptoLocker, etc.) | **Downloaded 2026-09-04, byte-exact verified** — not yet wired into training |

BitcoinHeist matters more than Elliptic for us specifically: it's
**address-level**, matching our wallet-scoring unit directly, whereas
Elliptic is transaction-level. UCI's own host truncates this file on every
attempt (no range support); a GitHub-hosted mirror of the same published
dataset works and is now documented in `data/README.md`.

**Not yet done:** actually training the wallet scorer on BitcoinHeist. Right
now it's downloaded and verified but sitting unused, same as Elliptic was
before the benchmark.

---

## 4. Where we think the project is weak, and the plan (not yet built)

Prompted by re-reading the problem statement against what's actually built:
**we do zero correlation between the network layer (IP/port) and the
blockchain layer.** All 14 model features are chain-only. `src_port`,
`dst_port`, and `script_type` are parsed and stored but never read anywhere
downstream. The IP nodes sit in the graph but carry no value and feed no
score. This is despite the problem statement's core ask being exactly that
correlation, and despite "cluster entities" being a stated objective we
don't do at all.

### The plan under discussion (talked through, nothing built yet)

**Core idea:** stop scoring individual wallets; identify *actors* by fusing
two independent ownership signals:
1. Chain-side: common-input-ownership (wallets that spend together)
2. Network-side: co-broadcast (wallets that always broadcast from the same
   IP, in tight time windows)

When both signals agree, that's strong evidence of one actor running
multiple wallets — a correlation no chain-only tool (Chainalysis, Elliptic's
own commercial product) can produce, because they never see the P2P layer.

This directly answers the rubric's two currently-weakest categories:
**Innovation** (genuinely uncommon, defensible under Q&A) and **Relevance**
(it's literally what the brief asks for and we don't currently do).

Ground truth already exists for measuring this: our Phase 1 generator
already makes every planted pattern broadcast from one dedicated,
100%-exclusive IP, so entity-resolution accuracy could be measured
immediately without new data work.

### Full priority order discussed

1. Entity resolution via co-broadcast + common-ownership fusion — the novelty spine
2. Wire BitcoinHeist into wallet-scorer training (now unblocked, file is downloaded)
3. Scalability + business-model write-up — cheapest marks available, currently near-zero on both
4. Cross-layer "contradiction" detectors (wallets that should share ownership by one signal but not the other) — nearly free once (1) exists
5. Counterfactual explanations ("this wallet clears the threshold if X changed") — stretch goal

### Rubric self-assessment discussed (10 categories × 10 marks)

| Criterion | Current estimate | Lever |
|---|---|---|
| Problem Understanding | ~7 | Name the user (FIU/cybercrime-cell investigator) explicitly |
| Innovation & Originality | ~5 | Cross-layer correlation |
| Relevance to Problem Statement | ~7 | Entity clustering is mandated, currently missing |
| Technical Approach & Architecture | ~9 | Already strong |
| Feasibility | ~9 | Don't over-scope |
| Prototype / PoC | ~9 | Already working end to end |
| Scalability | ~4 | Weakest row — no story yet for beyond one in-memory dataset |
| Impact & Usefulness | ~6 | Quantify investigator time saved |
| Business / Sustainability | ~2 | Near-zero — offline-appliance-for-air-gapped-LEA angle unused |
| Presentation & Communication | ~8 | Real-data honesty is a strong Q&A asset |

---

## 5. Immediate next action

Nothing above section 4 has been implemented — it was a planning discussion
only, deliberately paused before writing code so the direction could be
confirmed first. The next message in this conversation should say which of
the five numbered items to start on.
