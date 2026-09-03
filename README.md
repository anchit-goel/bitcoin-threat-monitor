# Bitcoin Transaction Threat Monitor

**SIH 2026 — AI-Powered Monitoring & Analysis of Bitcoin Transaction Traffic**

Ingests Bitcoin transaction metadata, builds a graph of wallets and IPs, scores
wallets for suspicious activity using a combination of machine learning and
domain rules, and presents the results on a dashboard with an interactive
link-analysis graph.

Every finding is explainable: each flagged wallet comes with plain-English
reasons drawn from SHAP feature attributions and from rule detectors that
encode known laundering patterns (peel chains, structuring, rapid layering,
round trips).

---

## Repository layout

```
/backend
  /app
    main.py             FastAPI entrypoint (CORS, endpoints)
    models.py           Pydantic schemas  <-- SOURCE OF TRUTH
    /services           Pipeline modules, added phase by phase
    /models_trained     Serialized .joblib models (gitignored)
  requirements.txt
  README.md
/frontend               Vite + React app (graph view + dashboard)
/data                   Generated datasets and downloaded Elliptic CSVs (gitignored)
README.md               This file
```

### `backend/app/models.py` is the contract

`Transaction` and `WalletAlert` in that file define the shapes the whole team
builds against — the generator emits them, the parser validates against them,
the scorer returns them, the API serves them, and the frontend renders them.

If your module needs a field that isn't there, **change `models.py` first and
tell the team.** Do not define a local variant. If generated code drifts from
these shapes, point it back at this file explicitly.

---

## Running locally

Two terminals. Backend first.

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

On PowerShell the activate line is `.venv\Scripts\Activate.ps1` instead.

Verify: http://localhost:8000/health returns `{"status": "ok"}`.
API docs: http://localhost:8000/docs

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Verify: http://localhost:5173 loads. The backend already allows CORS from that
origin. Point it elsewhere with `VITE_API_URL` if the backend is not on 8000.

The app has two views over one shared wallet detail panel:

- **Link analysis** — the force-directed graph. Wallets are circles coloured on
  a green-to-red risk ramp and sized by score; IP addresses are grey squares,
  so the two entity types never blur together. High and critical wallets carry
  a pulsing ring, which is what makes them findable without clicking. Opens on
  "Medium and up", because the unfiltered graph is ~1,700 nodes and 20,000
  links and reads as a hairball. **"Critical only" is the view to demo** — 210
  nodes where the planted structures are visible as distinct clusters.
- **Dashboard** — transactions processed, wallets scored, wallets flagged, a
  labelled severity distribution, and a sortable, filterable alert table.

Clicking a node or a table row opens the same panel, showing the risk score,
confidence, severity, the full plain-English reasons, and the wallet's
counterparties as chips you can click to walk the network.

**Upload dataset** in the header POSTs to `/ingest` and refreshes both views —
that is the live-ingestion moment for the demo.

### Order that matters

1. **Train the models first.** `python -m app.services.wallet_model` from
   `/backend`. The API loads them once at startup; without them it still comes
   up, but `/ingest` returns 503 and `/health` says why.
2. **POST a dataset to `/ingest`.** The graph lives in memory and starts empty,
   so `/alerts` and `/graph` return 409 until something is loaded. `/health`
   reports which of the two is missing, so a blank dashboard is never a mystery.

```bash
curl -X POST http://localhost:8000/ingest -F "file=@../data/synthetic_transactions.json"
```

## API

| Endpoint | What it does |
| --- | --- |
| `GET /health` | Liveness, plus whether models and a graph are loaded |
| `POST /ingest` | Upload JSON/CSV/XML, build the graph, score every wallet |
| `GET /alerts` | Scored wallets, highest risk first. `min_severity`, `limit` |
| `GET /wallet/{address}` | One alert plus its neighbourhood. `hops`, `limit` |
| `GET /graph` | The scored graph. `min_risk`, `limit`, `include_ips` |
| `DELETE /graph` | Drop the loaded dataset, for resetting between demo runs |

Interactive docs at http://localhost:8000/docs.

Measured on the 5,078-transaction demo dataset:

| Call | Time |
| --- | --- |
| `POST /ingest` (5,078 tx → 1,352 wallets scored) | 3.5 s |
| `GET /alerts?limit=100` | 0.22 s |
| `GET /graph?min_risk=0.8` | 0.31 s |
| `GET /wallet/{address}` | 1.0 s |

Two things worth knowing before you build against it:

- **`/graph` trims by default.** The full graph has ~20,000 links and will lock
  up a browser. It returns the 600 riskiest nodes unless you raise `limit`, and
  sets `truncated: true` when it has dropped anything. `?min_risk=0.8` gives a
  ~210-node view, which is the one to demo.
- **Explanations are built for the top 150 wallets at ingest**, and on demand
  for anything below that. A client cannot tell the difference — every alert
  returned carries its `top_reasons` — but it keeps ingest at 3.5 s instead of
  paying SHAP for a thousand wallets nobody opens.

### Nothing reaches the network

Geo enrichment reads a local MaxMind database at
`backend/app/data/GeoLite2-City.mmdb`. It is not committed — download
GeoLite2-City from MaxMind if you want geo data. Without it, `lookup` returns
None and everything else carries on; geo is enrichment, not a dependency.

`tests/test_api.py` asserts this rather than trusting it: it replaces every
outbound socket call with something that raises, then runs a full ingest and
score. Anything reaching for the network fails the test instead of quietly
succeeding on a machine that happens to be online.

---

## Build phases

| Phase | Deliverable | Owner | Status |
| --- | --- | --- | --- |
| 0 | Project scaffold + schema lock | Backend B | **done** |
| 1 | Synthetic data generator | Backend A | **done** |
| 2 | Ingestion parser + graph builder | Backend A | **done** |
| 3 | ML baseline on the Elliptic dataset | AI/ML | **done on real data** — see the accuracy warning above |
| 4 | Domain rule detectors | Cybersecurity | **done** |
| 5 | Graph features + scoring + explainability | AI/ML | **done** |
| 6 | API layer | Backend B | **done** |
| 7 | Frontend: graph visualization | Frontend A | **done** |
| 8 | Frontend: dashboard + stats | Frontend B | **done** |
| 9 | Full integration pass | Everyone | not started |

Pull before starting your phase; commit and push when you finish, so the next
dependent phase has your code to build on.

## Running the pipeline

From `/backend`, with the virtualenv active:

```bash
python -m app.services.data_generator
```

Generates `data/synthetic_transactions.json` (~5,000 transactions) and
`data/ground_truth.json`, which records exactly which wallets were planted and
with which pattern. Seeded, so it is reproducible.

```bash
python -m app.services.graph_builder
```

Loads that dataset, builds the wallet/IP graph, and writes `data/graph.json`
for inspection without Python.

```bash
python -m tests.test_domain_rules
```

Runs the four detectors against a hand-built graph and prints what each one
found, with its reasoning.

```bash
python -m app.services.ml_baseline
```

Trains the RandomForest and IsolationForest on the Elliptic dataset and saves
them to `app/models_trained/`. Requires the three Elliptic CSVs in `/data`
first — download "Elliptic Data Set" from Kaggle.

```bash
pytest tests/ -q
```

72 tests, no external data needed. The ML tests run against
`tests/elliptic_fixture.py`, which reproduces the Elliptic schema so the
pipeline stays covered on a fresh clone.

## Resolved: two feature spaces that do not meet

The Phase 5 brief said `score_wallet` should combine graph features with a rule
encoding into one vector and score it with the RandomForest from Phase 3.
**That could not work as written**, and option 1 below is what was built.

The Phase 3 models are trained on Elliptic's 166 anonymised columns, whose
meaning was never published. The graph features in `feature_extraction.py` —
degree, PageRank, velocity, amount variance — live in a completely different,
roughly 12-column space. There is no mapping between them; Elliptic's features
cannot be recomputed from our graph, and ours cannot be expressed in theirs.
Passing one to a model fitted on the other raises, which is the good case.

`app/models_trained/manifest.json` records the exact schema each model was
fitted on so the mismatch surfaces immediately rather than silently.

Realistic options for whoever takes Phase 5:

1. **Train a second model on our own feature space**, labelled from
   `data/ground_truth.json`, and use *that* inside `score_wallet`. Keep the
   Elliptic models as a separately reported benchmark. This is the honest
   version and the one that lets SHAP produce explanations an analyst can read,
   since our features have names that mean something.
2. Score wallets from the domain rules and graph features alone, and cite
   Elliptic purely as external validation that the approach generalises.

**Option 1 is what `wallet_model.py` does.** The Elliptic numbers still belong
in the write-up, just not in the same code path as the wallet scorer.
`scoring.py` raises a named error if an Elliptic-shaped model reaches it.

## Scoring accuracy

`wallet_model.py` trains on one seeded dataset and evaluates on a **separately
seeded** one — different wallets, different addresses, different injected
patterns. Training and testing on the same generated data would report
memorisation as accuracy.

Measured on the held-out seed, 1,332 wallets, 95 planted:

| Approach | Precision | Recall | F1 |
| --- | --- | --- | --- |
| Domain rules alone | 89% | 87% | 88% |
| Combined score >= 0.30 (medium+) | 84% | 98% | 90% |
| Combined score >= 0.55 (high+) | 91% | 96% | 93% |
| **Combined score >= 0.80 (critical)** | **99%** | **92%** | **95%** |

The rules and the model are genuinely complementary: of the 12 planted wallets
the rules miss, the model catches 10. Two findings worth knowing:

- **The rule flags add nothing to the model's accuracy.** Trained with and
  without them the forest scores identically — the graph features already
  carry the signal, led by transaction velocity at 25% importance. The rules
  earn their place as *evidence and explanation*, not as model input.
- Scoring is batched. Predicting one wallet at a time against a 300-tree forest
  cost ~90 ms per wallet; batching the whole matrix cut a full ingest from 122 s
  to 5.3 s, with identical scores (there is a test asserting exactly that).

```bash
python -m app.services.wallet_model
```

Trains and saves the wallet-space models. Run it before starting the API.

## Live demo

A published demo runs on GitHub Pages. It is the real frontend against a
**frozen snapshot** of real API responses — Pages serves static files and
cannot run FastAPI, so deploying the frontend alone would publish an app that
loads and then reports the API unreachable.

Everything read-only behaves exactly as it does against the live server; the
graph filters and the wallet neighbourhoods are recomputed client-side and
produce identical node and link counts. What the demo cannot do is ingest a
new file, because that needs the pipeline. The header says so instead of
offering a button that fails.

To refresh the snapshot after changing the data or the models:

```bash
# with the backend running and a dataset ingested
cd frontend
npm run snapshot        # writes public/demo-data/
git add public/demo-data && git commit -m "Refresh demo snapshot"
```

Pushing to `main` builds and deploys via `.github/workflows/pages.yml`. The
workflow fails early if `public/demo-data` is empty, rather than publishing a
broken site.

## Read this before quoting any accuracy number

There are two sets of figures in this repository and they say very different
things.

**On synthetic data we score 96-99%.** Those numbers are real, reproducible and
measured on a held-out seed — but the patterns being detected were planted by
`data_generator.py`, and the generator and the detectors were written against
the same idea of what a peel chain looks like. They answer "do we detect what we
drew?", not "does this work on real crime".

**On real data our structural features are close to useless.**
`python -m app.services.elliptic_real` trains on the Elliptic Data Set — 203,769
real Bitcoin transactions, 4,545 labelled illicit by Elliptic's own analysts —
using the six features from `feature_extraction.py` that an edge list supports.
Held out by connected component so no edge crosses the split:

| Measure | Result |
| --- | --- |
| ROC AUC (random forest) | **0.685** |
| Every feature individually | 0.387 – 0.498 (a coin flip is 0.500) |
| Precision@50 | **8.0% — 0.72x, worse than picking at random** |
| Precision@500 | 19.6% (1.75x over an 11.2% base rate) |
| Isolation forest ROC AUC | **0.358 — worse than chance** |

Two things follow, and both matter for the write-up.

Illicit transactions turn out to be *less* connected than licit ones, which is
why an anomaly detector fitted on licit data ranks them as more normal. The
isolation forest is not weak here, it is actively pointing the wrong way.

And at the sharp end of the queue — the top 50, which is what an analyst
actually reviews — the ranking is no better than chance. Modest lift only
appears 500 alerts deep.

**What this does and does not condemn.** It tests the structural half of the
feature set. The amount and timing features (velocity, totals, variance) could
not be tested, because Elliptic's edge list carries no values and the file that
does is 658 MB and truncates on every mirror reachable here. Those are precisely
the features most likely to hold the signal, and they remain unvalidated.

So: quote the synthetic numbers as *"detects the laundering patterns we
model"*, not as accuracy against real-world crime. The honest headline is that
topology alone does not separate real illicit Bitcoin activity, and that finding
came out of building the benchmark rather than assuming the synthetic score
generalised.

```bash
python -m app.services.elliptic_real
```

See `data/README.md` for the two files it needs and how to verify they are
genuine.

## Detector accuracy

Measured on the 1,352-wallet demo graph against `ground_truth.json`:

| Rule | Precision | Notes |
| --- | --- | --- |
| peel chain | 100% | all 3 planted chains fully recovered |
| structuring | 94% | both planted fanouts fully recovered |
| rapid layering | 92% | |
| round trip | 100% | originators and ring members |

Overall precision 96%, recall 93%, F1 94%, with a 0.40% false-positive rate on
clean wallets, at roughly 5 ms per wallet.

Thresholds in `domain_rules.py` were set by measurement, not by taste — the
module docstring records what each one is worth. If you change one, re-measure
against `ground_truth.json` rather than eyeballing the result.

---

## Design constraints

- **No outbound network calls at runtime.** Geo enrichment reads a local
  GeoLite2 database at `backend/app/data/GeoLite2-City.mmdb`.
- **Explainability is a requirement, not a nice-to-have.** A risk score with no
  reasons attached is not a finished alert.
