# Bitcoin Transaction Threat Monitor — Progress So Far

**SIH 2026 internal hackathon.** Status as of 2026-09-04 (evening), after the
CryptoTrace frontend merge and the neon geographic map. Team: this repo has
one other contributor, **anchit-goel**, who pushed a full frontend rewrite
directly to `main` (commit `e593ab0`) partway through this session — see
section 5.

Repo: https://github.com/Harit117/bitcoin-threat-monitor
Live demo: https://harit117.github.io/bitcoin-threat-monitor/ (static
snapshot of the *old* frontend — predates the CryptoTrace merge, see caveat
in section 5)

Latest commit: `7d76c16` — "Wire the CryptoTrace frontend to the real
backend - real entities, real dossiers, real geo flows"

---

## 1. What's built

A full offline pipeline, end to end: upload a transaction file → parse it →
build a wallet/IP graph → resolve wallets into actors (entity resolution) →
score every wallet with ML + hand-written rules → explain each score in
plain English → serve all of it to a React dashboard with a live, clickable
map.

**Stack:** FastAPI 0.141 / Pydantic 2.13 / networkx / scikit-learn / SHAP on
the backend; React 19 / TypeScript / Vite 8 / Tailwind v4 on the frontend
("CryptoTrace", anchit-goel's Figma Make scaffold, now fully wired to real
data — no map API key needed, see section 4). **98 backend tests, all
passing.** `tsc --noEmit` and `vite build` both clean on the frontend.

### The model that actually scores wallets (unchanged from earlier — this is what's live)

- **RandomForestClassifier** (300 trees, `class_weight="balanced"`) — supervised,
  trained on our synthetic ground truth
- **IsolationForest** (300 trees, contamination 0.0865) — unsupervised, fitted on
  clean wallets only
- Combined `risk_score = 0.75 × RF_probability + 0.25 × normalised_anomaly`
- 14 input features: 10 graph measures + 4 binary rule flags
- Explanations: SHAP `TreeExplainer`
- Held-out synthetic performance: precision 0.911, recall 0.968, F1 0.939
  — **quote this only as "detects the patterns we modelled," never as
  real-world accuracy** (see section 2)

### Domain rule detectors (4)

Peel chain, structuring/smurfing, rapid layering, round-trip/wash. Tuned
against measured ground truth: **96% precision, 93% recall, F1 94%** on the
synthetic dataset.

### Entity resolution (new since last progress note — `b54404b`)

The problem statement explicitly asks to correlate network-layer (IP/port/
timing) with blockchain-layer data and to "cluster entities." Built and
**now live in the API** (`/entities`, `/entities/{id}`, `/entities/matrix`):

- **Chain signal:** common-input-ownership (wallets that co-spend on the same tx)
- **Network signal:** co-broadcast (wallets broadcasting from the same source
  IP within a 2-hour window)
- An entity confirmed by *both* signals is the strongest evidence — this is
  the cross-layer correlation a chain-only tool (Chainalysis, Elliptic's own
  product) structurally cannot produce, since they never see the P2P layer.
- **Two real hub-poisoning bugs found and fixed** (not synthetic-data
  artifacts — a documented failure mode in real blockchain forensics): one
  wallet co-spending with 154 others chained the entire graph into one
  951-wallet "entity" via naive transitive clustering, driving precision to
  0%; the identical failure recurred on the network side via wallets that
  bridge multiple shared IPs. Fixed both by capping each wallet's
  contribution (`MAX_PERSONAL_CO_SPEND_DEGREE = 4`,
  `MAX_WALLET_IP_SPREAD = 2`).
- Result after both fixes: pairwise precision 0% → 11.0%, recall 11.3% →
  9.4%, F1 0.001 → 0.102, largest entity 951 wallets → 6.
- **Known, stated gap:** a wallet that never spends (only receives — every
  `rapid_fanout` target, every peel-chain "side" wallet) is invisible to
  both signals by construction, so `rapid_fanout` scores exactly 0% recall
  here — not a tuning gap, a structural limit of what these two signals can see.
- **Validation is synthetic-only, permanently** — no public dataset pairs
  real P2P capture with real blockchain ground truth, which is exactly why
  the problem statement supplies synthetic network data for this part.
- Does **not** feed the live wallet scorer — a second, complementary output,
  same discipline as the two real-data benchmarks below.

### ChainSentry — the real-data model (new since last progress note — `33588ed`)

Named model, trained and evaluated on **real** data: **BitcoinHeist**,
2,916,697 real Bitcoin addresses, 41,413 labelled to one of 28 real
ransomware families (WannaCry, Locky, Cerber, CryptoLocker, etc.), verified
byte-exact against the published dataset.

- **Split:** chronological cutoff at year 2016 (checked against 2017, not
  assumed) — 21 of 26 test-set ransomware families, including WannaCry,
  never appear in training; 5,328 addresses appearing on both sides of the
  cutoff are dropped from test explicitly, with a test asserting no leakage.
- **Operating threshold: 0.39** — the F2-optimal point from sweeping
  0.02–0.50, chosen deliberately for recall-over-precision (a missed
  ransomware address costs nothing visible; a false positive costs an
  analyst a few minutes of review). Buys recall 29.6% → 36.5% for almost no
  precision cost, then flattens before collapsing past ~0.10. **Honestly
  reported: there is no hidden sweet spot — the ranking itself is the
  bottleneck, not the cutoff.** Even at this point, 14.5% of the test
  population is flagged — too large for direct manual review at scale.
- **Ablation:** `income` is the top feature (45% importance) but actively
  hurts top-of-queue precision — dropping it lowers aggregate ROC AUC
  (0.693 → 0.547) while roughly **quadrupling precision@100** (0.56× → 2.25×
  base rate lift), because extreme-income addresses are legitimate
  high-volume wallets (exchanges, miners), not ransoms. Both configurations
  reported, not just the flattering one.
- **Does not feed the live wallet scorer** — different feature space
  (BitcoinHeist's own 6 address-level measures vs. our 10 graph features),
  same discipline as the Elliptic benchmark.

### The real-Elliptic finding (`a2c37f1`, still the most important caveat)

Benchmarked our *structural* graph features (degree, PageRank, clustering —
everything computable from an edge list alone, no amounts/timing) against
203,769 real Elliptic transactions, 4,545 labelled illicit. **Result: close
to useless** — ROC AUC 0.685, every feature individually 0.387–0.498 (0.5 =
coin flip), precision@50 8.0% (worse than random), isolation-forest AUC
0.358 (worse than chance). Illicit transactions are *less* connected than
licit ones on real data — the opposite of the synthetic assumption.
Amount/timing features couldn't be tested (Elliptic's features file is
undisclosed and unreachable, 658MB, truncates on every mirror). **The
untested amount/timing features are plausibly where the real signal is** —
and ChainSentry above is the closer analogue to testing that, since
BitcoinHeist's features are exactly amount/timing/velocity-shaped.

### Performance work

- Ingest 5,078 tx → 1,352 wallets: 12s → **3.5s** (networkx cache-signature
  check was O(V), not O(1))
- `/alerts` full page: batched ~1,200 separate SHAP calls into one pass,
  12s+ hang → **0.73s**

---

## 2. Real datasets in hand

| Dataset | What it is | Status |
|---|---|---|
| **Elliptic** (graph + labels) | 203,769 real transactions, 4,545 illicit | Used for the structural-feature benchmark above |
| **Elliptic features file** (166 cols) | Undisclosed meaning | Unreachable — 658MB, truncates on every mirror |
| **BitcoinHeist** | 2,916,697 real addresses, 41,413 labelled | **Trained — this is ChainSentry, above** |

---

## 3. Rubric self-assessment (10 categories × 10 marks) — updated

| Criterion | Estimate | Why it moved |
|---|---|---|
| Problem Understanding | ~7 | unchanged |
| Innovation & Originality | **~7** (was ~5) | Entity resolution is now real and live, not just discussed |
| Relevance to Problem Statement | **~8** (was ~7) | Entity clustering (explicitly mandated) now exists and is queryable via API |
| Technical Approach & Architecture | ~9 | unchanged |
| Feasibility | ~9 | unchanged |
| Prototype / PoC | **~9** | Full click-through demo works: actors → wallets → real money trail → real geo flows, no external API key required |
| Scalability | ~6 (was ~4) | Write-up now exists (`SCALABILITY_AND_BUSINESS.md`), still no built multi-dataset story |
| Impact & Usefulness | ~6 | unchanged — still needs a quantified investigator-time-saved framing |
| Business / Sustainability | ~6 (was ~2) | Write-up now exists |
| Presentation & Communication | **~9** | Real-data honesty (Elliptic finding, ChainSentry ablation) plus a live, visually strong demo is a genuine Q&A asset |

---

## 4. Frontend: CryptoTrace, wired to real data (today's work, `7d76c16`)

anchit-goel's frontend rewrite (`e593ab0`) shipped with entirely fictional
mock data (`mock.ts`) — invented actors ("Garantex," "Hydra darknet
marketplace"), invented money trails, invented geo flows. Fully replaced:

- **New backend endpoints:** `GET /entities`, `GET /entities/{id}`,
  `GET /entities/matrix`, `GET /wallet/{address}/dossier`, `GET /geo-flows`
  — all computed for real at ingest time from `entity_resolution.py` +
  the scored graph (`backend/app/services/entity_api.py`, new file).
- **New frontend API client** (`frontend/src/api.ts`) replacing `mock.ts`
  entirely (deleted). Every view — Investigation Board, actor detail, wallet
  dossier, money trail, Heatmap (real actor×actor BTC matrix), Geographic
  flow table — now shows real computed data, verified by clicking through
  every one of them in a running browser.
- **Two real bugs found and fixed in the inherited scaffold** (not
  something I introduced): `tsconfig.json` only loaded `@types/node`,
  silently dropping all `google.maps.*` type errors; and the installed
  `@googlemaps/js-api-loader` v2 removed the old `Loader.load()` method
  entirely, so the original code would have thrown at runtime.
- **One real bug found in the new backend code before shipping:** capping
  the actor list to 60 cards filtered each card's own `connected_actor_ids`
  but not the paired detail object, so a detail panel could reference an
  actor ID that no longer existed in the list — caught by testing
  `/entities` against `/entities/{id}` side by side, fixed by filtering both
  consistently.

## 5. The map: replaced Google Maps entirely (today, most recent work)

The request: a live map showing transaction flow between locations, no API
key, neon styling matching the dashboard theme, lines colored by
criticality, plus a fix for a hover-tooltip that vanished on mouseout.

**Google Maps is gone.** `GoogleGeoMap.tsx` deleted. In its place,
`frontend/src/NeonFlowMap.tsx` — a hand-built SVG world map (equirectangular
projection, simplified continent outlines) with:

- Glowing arcs between countries (SVG `feGaussianBlur` + `feMerge` filter),
  colored by the same CRITICAL/HIGH/MEDIUM/LOW tiers used everywhere else in
  the app, just in saturated neon hues (`#ff3b5c` / `#ffa634` / `#3ecbff` /
  `#39ff8a`) instead of the muted dashboard palette
- Animated flowing dashes (`stroke-dashoffset` CSS keyframe) plus a small
  traveling "packet" dot per arc (native SVG `animateMotion`) — gives a
  live, moving-transaction feel with zero JS animation loop
- Pulsing country markers, sized by max risk score of flows touching that
  country
- A `LIVE` badge and a color legend baked into the map itself
- **Real, not simulated, freshness:** `GeoFlowView` now polls
  `GET /geo-flows` every 8 seconds and shows an "UPDATED {time}" stamp — if
  the backend's data changes (re-ingest), the map picks it up without a page
  reload. The moving-dash/packet animation is a visual effect layered on top
  of real data, not fabricated motion.
- **Hover-vanish bug fixed:** the flow table's rows and the map's tooltip
  used to clear on `mouseleave`, so the detail card disappeared the instant
  the cursor moved. Now the last-hovered flow's card stays visible until a
  *different* row/arc is hovered or the new explicit `×` dismiss button is
  clicked. Verified in-browser: moved the mouse fully off the table, card
  stayed put; clicked `×`, card closed.

Verified: `npx tsc --noEmit` clean, `npx vite build` clean
(229KB bundle, down slightly from before since the Google Maps loader is no
longer imported), zero browser console errors, screenshots confirm real
glowing arcs rendering from real `/geo-flows` data.

**Caveat for the live demo:** the GitHub Pages deployment
(harit117.github.io) is a **static snapshot of the old frontend** — it
predates both the CryptoTrace merge and the neon map. It has not been
redeployed today. If the plan is to demo from that URL, it needs a fresh
static build + redeploy first; right now the only place the new frontend +
neon map can be seen is `npm run dev` locally.

---

## 6. Known gaps / not yet done

1. **GitHub Pages redeploy** — live demo URL is stale (see caveat above).
2. **Entity resolution does not feed the live wallet scorer** — by design,
   kept as a separate signal, same as the two real-data benchmarks.
3. **`rapid_fanout` pattern has 0% entity-resolution recall** — structural
   limit (receive-only wallets are invisible to both fusion signals), stated
   in the commit, not silently left as a bug.
4. **`.figma/make/site.json`** — a local-only placeholder file required for
   `vite.config.ts` to build (the teammate's Figma Make scaffold references
   an environment file Figma Make generates automatically but never
   exports/commits). It is correctly gitignored, but **not documented
   anywhere** as a required first-run step — a fresh clone (a teammate, or a
   judge) will hit `Could not resolve './.figma/make/site.json'` on first
   `npm run dev`/`vite build` with no explanation. Should get a line in
   `frontend/README.md` or the root `README.md`.
5. **Scalability Tier 2/3** (multi-dataset, distributed) — write-up exists,
   nothing built, not currently planned unless asked.

---

## 7. Immediate next actions (pick one — nothing below is started)

1. Redeploy the static GitHub Pages build so the public demo link matches
   what's actually been built (currently the single biggest gap between
   "what we can show live" and "what a judge clicking the README link sees")
2. Document the `.figma/make/site.json` local-setup step
3. Something else the user directs
