# Scalability & Business Case

**Bitcoin Transaction Threat Monitor — SIH 2026**

This is the write-up for the two rubric categories the project has genuinely
neglected. Every number below is either measured in this repository or is
explicitly labelled as an estimate — the same discipline applied to the
detector tuning and real-data benchmarks elsewhere in this project.

---

## Part 1 — Scalability

### We already have a track record, not just a plan

Three real capacity bottlenecks were found and fixed during development,
each with a measured before/after. That is the strongest evidence a judge
can be given that the scalability story below is engineering discipline,
not a slide written after the fact.

| Bottleneck found | Root cause | Before | After |
|---|---|---|---|
| Domain-rule pass over 1,352 wallets | A cache keyed itself on `graph.number_of_edges()`, which in networkx is **O(V)** — it sums degree across every node — so it cost 100M generator steps per lookup | 12.09 s | **2.20 s** |
| Scoring 1,352 wallets | One `predict_proba` call per wallet instead of one call for the whole matrix | 122 s | **5.3 s** |
| Explaining the riskiest wallets | One SHAP call per wallet (~1,200 individually) instead of one batched pass over the top 150 | minutes (found because it hung the dashboard) | **0.78 s** |

Full pipeline today, measured: **5,078 transactions → 1,352 wallets fully
scored and explained in 3.5 s.** Read endpoints, served from the resulting
in-memory cache: `/alerts` 0.22 s, `/graph` 0.31 s, `/wallet/{address}` 1.0 s.
Entity resolution — the cross-layer fusion — resolves the same dataset in
**0.01 s**.

### What the current architecture actually is, and its honest ceiling

The backend is one FastAPI process holding one `networkx` graph and one
alert cache in memory (`AppState` in `main.py`). `POST /ingest` replaces
that state atomically. This is a deliberate choice, not an oversight — it
matches the stated use case exactly: **one analyst, working one case, on
one machine, offline.** Building a multi-tenant clustered service for that
use case would have been solving a problem nobody has.

The honest ceiling of this architecture is set by two things: how much a
single process can hold in RAM, and how long a synchronous HTTP request is
allowed to block. Both are real, both are known, and neither is a surprise
that would show up for the first time in production.

### The scaling path, in three tiers

The point of laying this out is not that we will build all three tiers —
it is that we can name exactly where the current design stops working and
exactly what replaces it, tier by tier, rather than waving at "the cloud."

**Tier 1 — today.** Single machine, in-memory graph, synchronous ingest.
Comfortably handles a single case's worth of transaction data — tens of
thousands of wallets, low hundreds of thousands of edges — which is
`networkx`'s well-documented practical operating range on a single
machine. *(Standard practice for the library, not something we
benchmarked to its limit ourselves — 1,775 nodes / 20,721 edges is the
largest graph actually measured here.)*

**Tier 2 — a larger caseload, still one server.** Three changes, none of
them new research:
- Persist graphs to disk (Parquet or a pickle per case) instead of one
  global in-memory object, so multiple cases can be held and switched
  between without losing state on restart.
- Move ingest off the request thread into a background job with a
  status-poll endpoint, so a large file upload does not block for minutes
  the way a synchronous `POST` would.
- Add SQLite as a case index (metadata, not the graph itself), which is
  still a single file an investigator can back up or hand over as
  evidence, keeping the offline, no-server-admin property intact.

*Estimated ceiling: roughly the caseload of a regional financial-crime
unit — low millions of wallets — on a single well-specified server. This
is an engineering estimate based on where Tier 1's bottlenecks (RAM,
single-threaded ingest) actually sit, not a measured figure.*

**Tier 3 — full distributed deployment.** A genuine distributed-systems
project, correctly scoped as **future work, not a hackathon deliverable**
— and one thing about our design already leans toward it: **scoring one
wallet does not depend on scoring another** except through the shared
graph structure, which is the same kind of problem distributed graph
engines (Spark GraphX, Neo4j, Dask) already solve at production scale for
PageRank-style workloads. The path is a partitioned graph store, a
streaming ingest pipeline (Kafka-style) for continuous transaction feeds,
and periodic distributed rescoring — not a rewrite of the scoring logic
itself.

| Tier | Deployment | Ceiling (estimated beyond Tier 1) | New engineering required |
|---|---|---|---|
| 1 (today) | Single offline machine | Tens of thousands of wallets | None — built and measured |
| 2 | Single server, background jobs | Low millions of wallets | Job queue, on-disk case store |
| 3 | Distributed cluster | All of Bitcoin's active address space | Partitioned graph DB, streaming ingest |

### What does *not* need to change

The scoring model itself — the 14-feature wallet vector, the rule
detectors, the SHAP explanations — is agnostic to which tier serves it.
Moving from Tier 1 to Tier 2 to Tier 3 is a deployment and data-plane
question, not a retraining question. That separation was a deliberate
design choice (`scoring.py` never assumes anything about how the graph in
front of it was built or where it lives), and it is what keeps the
scaling story credible rather than "we'd have to start over."

---

## Part 2 — Business & Sustainability

### The problem statement already tells you the market

> *"Design and build a complete system **(offline)**..."*

That parenthetical is the entire business case in four words. Chainalysis,
Elliptic's own commercial product, and TRM Labs are cloud SaaS: live
subscription services, continuously updated proprietary attribution
databases, and — critically — **your case data leaves your building and
goes to a third party's servers.** For a large share of the realistic
buyers of a tool like this, that is not a pricing objection, it is a
non-starter.

### Who actually needs offline, and why

- **Law-enforcement cyber cells and financial intelligence units**
  working active cases, where evidence chain-of-custody and data
  sovereignty make routing case data through a foreign commercial cloud
  legally or operationally unacceptable. In India specifically: state
  police cyber cells, the Enforcement Directorate's financial-crime
  wing, and I4C-coordinated units are all plausible buyers under exactly
  this constraint — named here as the kind of institution this fits, not
  as a claimed relationship.
- **Air-gapped and classified environments**, where the machine doing
  the analysis is deliberately never on the internet, by policy — a
  cloud-dependent tool is disqualified before price ever enters the
  conversation.
- **Smaller or regional agencies** priced out of enterprise chain-analytics
  licensing, which runs to large annual sums for full-featured commercial
  tools. An offline, locally-run tool has no such recurring cloud-hosting
  cost baked into its price floor.
- **Training and academy use** — the synthetic data generator built for
  this project (planted peel chains, fanouts, round trips, with full
  ground truth) is a genuinely separate, sellable capability on its own:
  investigator training programs need realistic case data that is
  provably *not* an active real investigation, for exactly the reasons
  real case data cannot be used in a classroom.

### What we are honest about not having

Chainalysis and Elliptic have years of accumulated, proprietary
wallet-attribution intelligence — which real exchange addresses, which
known darknet-market wallets, which sanctioned entities. That is a real,
hard-won asset and this project does not have it, and pretending
otherwise would not survive five minutes of a technical Q&A. The honest
position: this system is strong exactly where a commercial black box is
structurally weak (network-layer correlation, on-premises deployment,
auditable and rule-explainable output), and it deliberately does not try
to compete on years of accumulated attribution data it has not had the
years to accumulate.

### Revenue model

Not a consumer SaaS. A licensed deployment to public-sector and
enterprise-compliance buyers, following the model that real forensic
software already uses successfully (the Cellebrite/Magnet Forensics
pattern in the adjacent device-forensics market):

1. **Per-agency deployment license** — installed on the buyer's own
   infrastructure, one-time or annual, no dependency on us being online
   for it to keep working.
2. **Support and update contract** — this, not the license, is where a
   forensic tool business actually sustains itself long-term: rule-set
   updates as new laundering techniques emerge, integration support for
   the buyer's existing case-management systems, and training.
3. **Training/curriculum licensing** — the synthetic-data and dashboard
   combination, licensed separately to academies as a lower-friction
   entry point that can lead into the investigative-tool sale.
4. **Open-core positioning** — publishing the core detection logic and
   rule set as auditable, inspectable code is not just an ethics
   position; for a tool whose output may end up cited in a legal
   proceeding, "you can read exactly why this alert fired" is a real
   requirement a closed commercial model cannot offer, and it is a
   genuine differentiator worth stating as one.

### Sustainability is a process, not a promise

The strongest evidence this project can offer that its own accuracy
claims will stay honest over time is that it already has the discipline
built in, demonstrated, not merely asserted:

- The domain-rule detectors were tuned from 9% precision to 96% by
  **measuring** against planted ground truth, not by intuition.
- Every real-data claim in this project (Elliptic, BitcoinHeist) is
  checked against the *published* dataset figures on load, so a
  corrupted or substituted download fails loudly rather than silently
  producing a plausible-looking number.
- The synthetic generator gives a repeatable regression harness: any
  future rule change, feature addition, or model update can be checked
  against known ground truth before it ever reaches a real case.

That discipline — not a roadmap slide — is the actual sustainability
story: the tool's accuracy claims are built to be checked, by us and by
whoever eventually operates it, rather than taken on faith.
