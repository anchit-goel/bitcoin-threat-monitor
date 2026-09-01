"""FastAPI entrypoint for the Bitcoin Transaction Threat Monitor.

Run locally with:
    uvicorn app.main:app --reload --port 8000
from inside the /backend directory.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Bitcoin Transaction Threat Monitor",
    description=(
        "Ingests Bitcoin transaction metadata, builds a wallet/IP graph, scores "
        "wallets for suspicious activity, and serves results to the dashboard."
    ),
    version="0.1.0",
)

# The Vite dev server runs on 5173. 127.0.0.1 is listed alongside localhost
# because browsers treat them as distinct origins.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Phase 6 extends this to report whether a graph is loaded."""
    return {"status": "ok"}
