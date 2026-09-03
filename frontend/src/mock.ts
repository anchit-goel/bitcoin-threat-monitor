// ─── API mock data ────────────────────────────────────────────────────────
// All shapes mirror the API contract. To swap for real endpoints, replace
// each `api.*` function body with a `fetch(…).then(r => r.json())` call.

export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'

// ─── Shapes ───────────────────────────────────────────────────────────────
export interface Actor {
  actor_id: string
  member_wallet_ids: string[]
  aggregate_risk_score: number
  confidence: number
  short_summary: string
  connected_actor_ids: string[]
}

export interface ActorDetail extends Actor {
  top_reasons: string[]
  actor_connections: Array<{
    target_actor_id: string
    link_type: string
    amount_btc: number
  }>
}

export interface WalletDetail {
  wallet_id: string
  address: string
  address_full: string
  risk_score: number
  confidence: number
  severity: Severity
  first_seen: string
  last_active: string
  tx_count: number
  total_volume_btc: number
  velocity_data: number[]
  ai_narrative: string
  contributing_features: Array<{ name: string; raw: number; max: number; unit: string }>
  connected_wallets: Array<{ address: string; risk_score: number; severity: Severity; relation: string }>
  trail: TrailHop[]
}

export interface AlertItem {
  wallet_id: string
  address: string
  risk_score: number
  confidence: number
  severity: Severity
  reason: string
  flagged_at: string
}

export interface GeoFlow {
  from_country: string
  to_country: string
  amount: number
  risk_score: number
}

export interface TrailHop {
  step: number
  from_wallet: string
  to_wallet: string
  to_label: string
  amount_btc: number
  amount_usd: number
  timestamp: string
  tx_hash: string
  to_score: number
  to_severity: Severity
}

// ─── GET /actors ──────────────────────────────────────────────────────────
const ACTORS: Actor[] = [
  {
    actor_id: 'ACT-001',
    member_wallet_ids: ['w1', 'w2', 'w3'],
    aggregate_risk_score: 91,
    confidence: 86,
    short_summary: 'Sanctioned exchange cluster — direct Garantex exposure, 3-hop DNM link',
    connected_actor_ids: ['ACT-003', 'ACT-005'],
  },
  {
    actor_id: 'ACT-002',
    member_wallet_ids: ['w4', 'w5'],
    aggregate_risk_score: 74,
    confidence: 79,
    short_summary: 'Mixing service operator — 47 rapid micro-tx in 6-hour window',
    connected_actor_ids: ['ACT-001', 'ACT-004'],
  },
  {
    actor_id: 'ACT-003',
    member_wallet_ids: ['w6', 'w7', 'w8'],
    aggregate_risk_score: 78,
    confidence: 83,
    short_summary: 'Hydra darknet marketplace wallet cluster — peel chain origin',
    connected_actor_ids: ['ACT-001', 'ACT-006'],
  },
  {
    actor_id: 'ACT-004',
    member_wallet_ids: ['w9'],
    aggregate_risk_score: 65,
    confidence: 71,
    short_summary: 'Peel chain operator — 12 sequential single-output transactions',
    connected_actor_ids: ['ACT-002'],
  },
  {
    actor_id: 'ACT-005',
    member_wallet_ids: ['w10', 'w11'],
    aggregate_risk_score: 52,
    confidence: 64,
    short_summary: 'Coinjoin participant with high-risk counterparty exposure',
    connected_actor_ids: ['ACT-001', 'ACT-007'],
  },
  {
    actor_id: 'ACT-006',
    member_wallet_ids: ['w12'],
    aggregate_risk_score: 45,
    confidence: 58,
    short_summary: 'Dormant wallet reactivated 847 days dormant — large immediate outflow',
    connected_actor_ids: ['ACT-003'],
  },
  {
    actor_id: 'ACT-007',
    member_wallet_ids: ['w13'],
    aggregate_risk_score: 31,
    confidence: 72,
    short_summary: 'Off-hours transaction timing anomaly — potential structuring behavior',
    connected_actor_ids: ['ACT-005'],
  },
  {
    actor_id: 'ACT-008',
    member_wallet_ids: ['w14', 'w15'],
    aggregate_risk_score: 22,
    confidence: 69,
    short_summary: 'Minor indirect exposure — 4-hop removal from flagged cluster',
    connected_actor_ids: [],
  },
]

// ─── GET /actors/{id} ─────────────────────────────────────────────────────
const ACTOR_DETAILS: Record<string, ActorDetail> = {
  'ACT-001': {
    ...ACTORS[0],
    top_reasons: [
      'Direct 3-hop connection to Garantex exchange (OFAC sanctioned Feb 2022)',
      'Peel chain pattern: 14 wallets, 48-hour dispersion window post-receipt',
      'Funds received by known Hydra marketplace cluster (ACT-003)',
      'Transaction amounts consistently just below CTR threshold',
    ],
    actor_connections: [
      { target_actor_id: 'ACT-003', link_type: 'shared_funding', amount_btc: 4.21 },
      { target_actor_id: 'ACT-005', link_type: 'shared_funding', amount_btc: 1.84 },
    ],
  },
  'ACT-002': {
    ...ACTORS[1],
    top_reasons: [
      '47 micro-transactions under 0.001 BTC each in a 6-hour window',
      'Mixing coordinator wallet confirmed in hop analysis',
      'Shared funding source with ACT-001 (Garantex cluster)',
    ],
    actor_connections: [
      { target_actor_id: 'ACT-001', link_type: 'shared_funding', amount_btc: 2.10 },
      { target_actor_id: 'ACT-004', link_type: 'shared_funding', amount_btc: 0.88 },
    ],
  },
  'ACT-003': {
    ...ACTORS[2],
    top_reasons: [
      'Three member wallets confirmed as Hydra marketplace deposit addresses',
      'Peel chain origin: 12 sequential single-output transactions sourced here',
      'Shared funding with ACT-001; indirect link to sanctioned exchange',
    ],
    actor_connections: [
      { target_actor_id: 'ACT-001', link_type: 'shared_funding', amount_btc: 4.21 },
      { target_actor_id: 'ACT-006', link_type: 'shared_funding', amount_btc: 1.12 },
    ],
  },
  'ACT-004': {
    ...ACTORS[3],
    top_reasons: [
      'Peel chain terminus: received final leg of 12-hop dispersion sequence',
      'Source funds trace back to ACT-002 mixing coordinator',
    ],
    actor_connections: [
      { target_actor_id: 'ACT-002', link_type: 'shared_funding', amount_btc: 0.88 },
    ],
  },
  'ACT-005': {
    ...ACTORS[4],
    top_reasons: [
      'Coinjoin participant — counterparty wallet scores 81 (HIGH)',
      'Indirect shared funding source with ACT-001',
    ],
    actor_connections: [
      { target_actor_id: 'ACT-001', link_type: 'shared_funding', amount_btc: 1.84 },
      { target_actor_id: 'ACT-007', link_type: 'shared_funding', amount_btc: 0.41 },
    ],
  },
  'ACT-006': {
    ...ACTORS[5],
    top_reasons: [
      '847-day dormancy followed by 18.2 BTC outflow in a single transaction',
      'Linked via ACT-003 to Hydra cluster',
    ],
    actor_connections: [
      { target_actor_id: 'ACT-003', link_type: 'shared_funding', amount_btc: 1.12 },
    ],
  },
  'ACT-007': {
    ...ACTORS[6],
    top_reasons: [
      'Consistent off-hours pattern: 02:00–04:00 UTC across 14 days',
      'Transaction amounts consistent with potential structuring (sub-threshold)',
    ],
    actor_connections: [
      { target_actor_id: 'ACT-005', link_type: 'shared_funding', amount_btc: 0.41 },
    ],
  },
  'ACT-008': {
    ...ACTORS[7],
    top_reasons: [
      'Indirect exposure: flagged address 4 hops removed, total exposure 0.012 BTC',
    ],
    actor_connections: [],
  },
}

// ─── GET /wallet/{id} ─────────────────────────────────────────────────────
const WALLET_BASE: WalletDetail = {
  wallet_id: 'w1',
  address: 'bc1q4x3wl5p9…8m2n',
  address_full: 'bc1q4x3wl5p9vr8zk2j6m3nq5t7y8w9e0r1t2y3u4i8m2n',
  risk_score: 94,
  confidence: 87,
  severity: 'CRITICAL',
  first_seen: '2024-04-17',
  last_active: '2026-08-29',
  tx_count: 147,
  total_volume_btc: 23.41,
  velocity_data: [2, 3, 2, 4, 3, 5, 4, 3, 6, 9, 14, 21, 30, 35, 24, 31],
  ai_narrative: `This wallet exhibits a layering pattern consistent with placement-stage funds movement
through a sanctioned exchange. Analysis identified a 3-hop connection to a wallet cluster
associated with Garantex (OFAC-sanctioned, February 2022).

The wallet received 2.8 BTC across 6 transactions between April and June 2026. Funds were
subsequently dispersed to 14 destination addresses within a 71-hour window — consistent
with distribution following a mixing or layering operation.

Confidence is elevated at 87% due to corroborating structural signals: consistent peeling
chains, off-hours activity (02:00–04:00 UTC), and transaction amounts just below common
reporting thresholds.`,
  contributing_features: [
    { name: 'Hop distance to sanctioned entity', raw: 3,   max: 10,  unit: ' hops' },
    { name: 'Transaction fan-out ratio',         raw: 14,  max: 20,  unit: ':6' },
    { name: 'Time-to-disperse after receipt',    raw: 71,  max: 168, unit: 'h' },
    { name: 'Mixing pattern indicator',          raw: 87,  max: 100, unit: '/100' },
    { name: 'Exchange cluster proximity',        raw: 94,  max: 100, unit: '/100' },
    { name: 'Velocity spike magnitude',          raw: 4.2, max: 10,  unit: 'x' },
  ],
  connected_wallets: [
    { address: '3FZbgi…rN5E', risk_score: 76, severity: 'HIGH',     relation: '1-hop origin' },
    { address: '1GarEx…xTUv', risk_score: 91, severity: 'CRITICAL', relation: '2-hop — sanctioned entity' },
    { address: 'bc1qmix…7kL2', risk_score: 83, severity: 'HIGH',    relation: 'Mixing coordinator' },
    { address: '1HydrA…k9Pz', risk_score: 97, severity: 'CRITICAL', relation: 'Known DNM cluster' },
    { address: 'bc1qsrc…2vBn', risk_score: 71, severity: 'HIGH',    relation: 'Relay node' },
  ],
  trail: [
    {
      step: 1, from_wallet: 'bc1q4x3wl5p9…8m2n', to_wallet: '3FZbgim8qV…rN5E',
      to_label: 'Intermediate relay', amount_btc: 2.8041, amount_usd: 174882,
      timestamp: '2026-06-14 03:22:17 UTC', tx_hash: '4a5e1e4baa6c…2c9f',
      to_score: 76, to_severity: 'HIGH',
    },
    {
      step: 2, from_wallet: '3FZbgim8qV…rN5E', to_wallet: '1HydrA77x3…k9Pz',
      to_label: 'Known DNM cluster — Hydra-linked', amount_btc: 2.7996, amount_usd: 174601,
      timestamp: '2026-06-14 03:41:05 UTC', tx_hash: '7f3c2d9ab21…8e1a',
      to_score: 97, to_severity: 'CRITICAL',
    },
    {
      step: 3, from_wallet: '1HydrA77x3…k9Pz', to_wallet: 'bc1qxy2kgd…9k1p',
      to_label: 'Peel chain terminus', amount_btc: 2.7951, amount_usd: 174320,
      timestamp: '2026-06-14 04:12:33 UTC', tx_hash: '9e1b4f2cc34…7d5b',
      to_score: 68, to_severity: 'HIGH',
    },
  ],
}

const WALLETS: Record<string, WalletDetail> = {
  w1:  { ...WALLET_BASE, wallet_id: 'w1',  address: 'bc1q4x3wl5…8m2n', risk_score: 94, severity: 'CRITICAL', confidence: 87 },
  w2:  { ...WALLET_BASE, wallet_id: 'w2',  address: '1A1zP1eP5Q…4BtX', risk_score: 81, severity: 'HIGH',     confidence: 79 },
  w3:  { ...WALLET_BASE, wallet_id: 'w3',  address: '3FZbgim8qV…rN5E', risk_score: 76, severity: 'HIGH',     confidence: 83 },
  w4:  { ...WALLET_BASE, wallet_id: 'w4',  address: 'bc1qxy2kgd…9k1p', risk_score: 68, severity: 'HIGH',     confidence: 71 },
  w5:  { ...WALLET_BASE, wallet_id: 'w5',  address: '1BpEi9xAdX…xHAM', risk_score: 52, severity: 'MEDIUM',   confidence: 64 },
  w6:  { ...WALLET_BASE, wallet_id: 'w6',  address: '3J98t1WpEZ…wHYz', risk_score: 78, severity: 'HIGH',     confidence: 80 },
  w7:  { ...WALLET_BASE, wallet_id: 'w7',  address: 'bc1qar0srr…dkJ9', risk_score: 71, severity: 'HIGH',     confidence: 74 },
  w8:  { ...WALLET_BASE, wallet_id: 'w8',  address: '1FeexV4eb6…FGsN', risk_score: 65, severity: 'HIGH',     confidence: 69 },
  w9:  { ...WALLET_BASE, wallet_id: 'w9',  address: 'bc1qzw9m4n…xK3p', risk_score: 65, severity: 'HIGH',     confidence: 71 },
  w10: { ...WALLET_BASE, wallet_id: 'w10', address: '1LdRcdxfb…wNYe', risk_score: 52, severity: 'MEDIUM',   confidence: 64 },
  w11: { ...WALLET_BASE, wallet_id: 'w11', address: 'bc1qtgq5xt…9Jp3', risk_score: 47, severity: 'MEDIUM',   confidence: 61 },
  w12: { ...WALLET_BASE, wallet_id: 'w12', address: '3PbJ4jNmG2…rQ7k', risk_score: 45, severity: 'MEDIUM',   confidence: 58 },
  w13: { ...WALLET_BASE, wallet_id: 'w13', address: 'bc1qnp8msd…hT4v', risk_score: 31, severity: 'LOW',      confidence: 72 },
  w14: { ...WALLET_BASE, wallet_id: 'w14', address: '17VZNX1SM…vBRg', risk_score: 22, severity: 'LOW',      confidence: 69 },
  w15: { ...WALLET_BASE, wallet_id: 'w15', address: 'bc1qpv8ndf…cZ2m', risk_score: 18, severity: 'LOW',      confidence: 65 },
}

// ─── GET /alerts ──────────────────────────────────────────────────────────
const ALERTS: AlertItem[] = Object.values(WALLETS).map(w => ({
  wallet_id: w.wallet_id,
  address: w.address,
  risk_score: w.risk_score,
  confidence: w.confidence,
  severity: w.severity,
  reason: WALLET_BASE.contributing_features[0].name,
  flagged_at: '2026-08-29 14:32',
}))

// ─── GET /geo-flows ───────────────────────────────────────────────────────
const GEO_FLOWS: GeoFlow[] = [
  { from_country: 'Russia',      to_country: 'UAE',         amount: 4.21, risk_score: 91 },
  { from_country: 'UAE',         to_country: 'Singapore',   amount: 3.89, risk_score: 85 },
  { from_country: 'Russia',      to_country: 'China',       amount: 2.11, risk_score: 82 },
  { from_country: 'Russia',      to_country: 'Germany',     amount: 1.44, risk_score: 78 },
  { from_country: 'UAE',         to_country: 'Switzerland', amount: 1.92, risk_score: 76 },
  { from_country: 'Nigeria',     to_country: 'UK',          amount: 0.52, risk_score: 72 },
  { from_country: 'Singapore',   to_country: 'Japan',       amount: 0.71, risk_score: 58 },
  { from_country: 'China',       to_country: 'USA',         amount: 0.87, risk_score: 65 },
  { from_country: 'Brazil',      to_country: 'USA',         amount: 0.34, risk_score: 41 },
]

// ─── Actor×Actor intensity matrix (8×8) for heatmap ──────────────────────
export const ACTOR_MATRIX: number[][] = [
  [ 0, 72, 88, 12, 54,  8,  4,  0],  // ACT-001
  [72,  0, 18, 65, 22,  5,  0,  0],  // ACT-002
  [88, 18,  0, 10, 14, 61,  0,  0],  // ACT-003
  [12, 65, 10,  0,  8,  2,  0,  0],  // ACT-004
  [54, 22, 14,  8,  0,  5, 41,  0],  // ACT-005
  [ 8,  5, 61,  2,  5,  0,  0,  0],  // ACT-006
  [ 4,  0,  0,  0, 41,  0,  0,  0],  // ACT-007
  [ 0,  0,  0,  0,  0,  0,  0,  0],  // ACT-008
]

// ─── API surface — swap bodies for real fetch() calls ────────────────────
const delay = () => new Promise<void>(r => setTimeout(r, 60 + Math.random() * 80))

export const api = {
  getAlerts:     async (): Promise<AlertItem[]>             => { await delay(); return ALERTS },
  getActors:     async (): Promise<Actor[]>                 => { await delay(); return ACTORS },
  getActorDetail: async (id: string): Promise<ActorDetail> => { await delay(); return ACTOR_DETAILS[id] },
  getWallet:     async (id: string): Promise<WalletDetail> => { await delay(); return WALLETS[id] ?? WALLET_BASE },
  getGeoFlows:   async (): Promise<GeoFlow[]>              => { await delay(); return GEO_FLOWS },
}
