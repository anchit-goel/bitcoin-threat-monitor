# Backend — Bitcoin Transaction Threat Monitor

FastAPI service that ingests transaction metadata, builds a wallet/IP graph,
scores wallets with a mix of ML models and domain rules, and serves the
results to the React dashboard.

## Setup

From this `/backend` directory:

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash); use .venv\Scripts\activate on PowerShell
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

Then check http://localhost:8000/health — it should return `{"status": "ok"}`.
Interactive API docs are at http://localhost:8000/docs.

## Layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI app + endpoints |
| `app/models.py` | **Pydantic schemas — the team's source of truth** |
| `app/services/` | Pipeline modules (data generation, ingestion, graph, ML, scoring) |
| `app/models_trained/` | Serialized `.joblib` models (created in Phase 3, gitignored) |

## Conventions

- Everything is typed against `app/models.py`. Do not redefine `Transaction`
  or `WalletAlert` locally — import them.
- Nothing at runtime may make an outbound network call. Geo enrichment reads a
  local GeoLite2 database from `app/data/GeoLite2-City.mmdb`.
