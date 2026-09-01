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
origin.

### Order that matters

- The ML models must be trained and saved (Phase 3) **before** the API tries to
  load them at startup.
- A dataset must be POSTed to `/ingest` **before** `/alerts` or `/graph` return
  anything — the graph lives in memory and starts empty.

---

## Build phases

| Phase | Deliverable | Owner | Status |
| --- | --- | --- | --- |
| 0 | Project scaffold + schema lock | Backend B | **done** |
| 1 | Synthetic data generator | Backend A | **done** |
| 2 | Ingestion parser + graph builder | Backend A | **done** |
| 3 | ML baseline on the Elliptic dataset | AI/ML | blocked — needs the Elliptic CSVs in `/data` |
| 4 | Domain rule detectors | Cybersecurity | **done** |
| 5 | Graph features + scoring + explainability | AI/ML | not started (needs 3) |
| 6 | API layer | Backend B | not started |
| 7 | Frontend: graph visualization | Frontend A | not started |
| 8 | Frontend: dashboard + stats | Frontend B | not started |
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
found, with its reasoning. `pytest tests/ -q` runs the same file as assertions.

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
