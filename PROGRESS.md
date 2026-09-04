# Bitcoin Transaction Threat Monitor — Progress So Far

**SIH 2026 internal hackathon.** Status as of 2026-09-05, after the
CryptoTrace frontend merge, the real-geometry neon map, and a working
GitHub Pages deploy. Team: this repo has one other contributor,
**anchit-goel**, who pushed a full frontend rewrite directly to `main`
(commit `e593ab0`) partway through this session — see section 4.

Repo: https://github.com/Harit117/bitcoin-threat-monitor
**Live demo: https://harit117.github.io/bitcoin-threat-monitor/ — verified
working**, full click-through, zero console errors, entirely from a real
static data snapshot (no backend needed to view it — see section 6).

Latest commit: `d77fd6e` — "Fix vite.config.ts crashing every build outside
Figma Make"

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

## 5. The map: real geometry, paginated, fully click-through

**Google Maps is gone**, replaced twice over. First pass:
`frontend/src/NeonFlowMap.tsx`, hand-drawn continent polygons with a neon
glow treatment. Second pass, after "make it look proper, not like this":
those hand-drawn shapes were replaced with **real Natural Earth geometry**
(`world-atlas` + `d3-geo`'s `geoNaturalEarth1` projection, ~108KB of
public-domain map data bundled into the app — zero API key, zero runtime
network call, works fully offline). Real coastlines, real country borders.

- Arcs colored by the same CRITICAL/HIGH/MEDIUM/LOW tiers used everywhere
  else in the app (`frontend/src/theme.ts`, pulled out as the one shared
  source of truth so the map can't drift from the badges), saturated for
  glow visibility. Animated flowing dashes plus a traveling "packet" dot per
  arc (native SVG `animateMotion`, no JS animation loop).
- **Real bug found and fixed in the risk computation, not just the visuals.**
  The map only ever showed LOW/MEDIUM despite 119 high-risk wallets existing
  in the data. Root cause, measured: a country-corridor's risk score
  *averaged* every wallet-pair's risk across the whole corridor (diluting a
  few CRITICAL wallets into a sea of ordinary transfers) and flows were
  ranked by BTC amount alone (so small high-risk transfers never survived
  the top-40 cut). Fixed by switching to a **BTC-volume-weighted average
  risk per corridor** and sorting risk-first. A pure max was tried first and
  rejected after measuring it saturated 34/40 corridors to CRITICAL; the
  weighted average gives an honest spread — 8 CRITICAL / 1 HIGH / 3 MEDIUM /
  28 LOW on this dataset.
- **List and map are synced.** The flow list is paginated (8/page); the map
  renders exactly the current page's flows, so the two can never show a
  mismatched set. Verified: paging from page 1 (all CRITICAL) to page 3 (all
  LOW) changes both the table and the map's line colors together.
- **Click for a full detail panel**, not just hover. Clicking a line or a
  row opens a slide-in panel (same visual language as the actor/wallet
  cards) listing the real wallet-to-wallet transfers behind that corridor —
  a new backend field, `GeoFlow.sample_wallets`, not a client-side
  invention — each one openable into its real wallet dossier. Heatmap cells
  are clickable too, opening the same `ActorDetailPanel` Investigation Board
  uses. All three views share one wallet-dossier overlay now.
- Hover tooltips no longer vanish on mouseout (they stick to the
  last-hovered item until a new hover or an explicit `×`).

Verified: 98 backend tests pass, `tsc --noEmit` and `vite build` both clean,
full click-through in a real browser with zero console errors.

---

## 6. GitHub Pages: two real deploy-blocking bugs found and fixed

The Pages workflow (`.github/workflows/pages.yml`) predates the CryptoTrace
rewrite and was pointed at infrastructure that no longer existed — the
**last two deploy attempts had been silently failing** (confirmed via
`gh run list`, not assumed). Two separate bugs, both fixed:

1. **No static-data layer for the new frontend.** `api.ts` only ever talked
   to a live backend; GitHub Pages can't run FastAPI. Added a
   `VITE_STATIC_DEMO` mode that reads pre-fetched JSON instead, and
   `backend/scripts/export_demo_snapshot.py`, which crawls a running backend
   for **every** real response the frontend can reach — all 1,352 wallet
   alerts, all 60 actor cards and details, the actor matrix, all 40
   geo-flows, and (BFS outward through every connected wallet and
   money-trail hop) **all 1,352 wallet dossiers** — so no link anywhere in
   the UI 404s on the published demo. `vite.config.ts`'s `base` also only
   read a Figma-specific env var, never the `BASE_PATH` the workflow
   actually sets, which would have 404'd every asset under the
   `/bitcoin-threat-monitor/` subpath.
2. **`vite.config.ts` crashed on every build outside Figma Make.** A static
   `import` of `.figma/make/site.json` — a file Figma Make's own hosted
   environment generates automatically and which is correctly gitignored —
   made the build fail outright anywhere that file doesn't exist, which is
   everywhere except inside Figma Make itself. This is what was actually
   breaking CI (confirmed from the failed run's own build log). Fixed to
   load the file defensively (`existsSync` + fallback to `{}`) instead.

Verified end to end before pushing: built with the exact env vars CI sets,
served the output locally under a `/bitcoin-threat-monitor/` subpath with
**no backend running at all**, clicked through every view including the
map's click-to-wallet-dossier path. Then verified the actual deploy: watched
the GitHub Actions run to green (`gh run watch`) and loaded the real public
URL — real data, real map, zero console errors, zero failed requests.

---

## 7. Known gaps / not yet done

1. **Entity resolution does not feed the live wallet scorer** — by design,
   kept as a separate signal, same as the two real-data benchmarks.
2. **`rapid_fanout` pattern has 0% entity-resolution recall** — structural
   limit (receive-only wallets are invisible to both fusion signals), stated
   in the commit, not silently left as a bug.
3. **Static demo drifts from the live backend over time.** The Pages
   snapshot is frozen at whatever `export_demo_snapshot.py` captured — if
   the model, entity resolution, or dataset changes, the public demo won't
   reflect it until the script is re-run and committed. Not automated; a
   manual step by design (the workflow comment says so).
4. **Scalability Tier 2/3** (multi-dataset, distributed) — write-up exists,
   nothing built, not currently planned unless asked.

---

## 8. Immediate next actions (pick one — nothing below is started)

1. Re-run `export_demo_snapshot.py` and redeploy whenever the model or
   dataset changes, so the public demo doesn't quietly drift stale again
2. Document the `.figma/make/site.json` gotcha in `frontend/README.md` so a
   fresh clone's first `npm run dev` isn't a mystery (the crash itself is
   now fixed, but the file's purpose still isn't explained anywhere)
3. Something else the user directs
