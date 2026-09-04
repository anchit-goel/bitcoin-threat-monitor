// ─── Real API client ──────────────────────────────────────────────────────
// Replaces mock.ts. Shapes mirror the backend's Pydantic models in
// backend/app/models.py almost field-for-field by design - ActorCard,
// ActorDetail, WalletDossier, GeoFlow were written to match this frontend's
// existing TypeScript interfaces, so most of what follows is a direct pass
// through fetch(), not a transform.

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'

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

export interface GeoFlowWallet {
  from_wallet: string
  to_wallet: string
  amount_btc: number
  risk_score: number
}

export interface GeoFlow {
  from_country: string
  to_country: string
  amount: number
  risk_score: number
  sample_wallets: GeoFlowWallet[]
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

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// GitHub Pages serves static files only - it cannot run FastAPI - so the
// published demo reads a pre-fetched JSON snapshot instead of hitting a live
// backend. Every request path used below has a matching static file,
// generated for real from a running backend by
// backend/scripts/export_demo_snapshot.py (see that script's docstring).
// This mirrors exactly what the live endpoint would have returned - nothing
// here is invented for the static build.
const STATIC_DEMO = import.meta.env.VITE_STATIC_DEMO === 'true'

function staticSnapshotFile(path: string): string {
  const [route, query] = path.split('?')
  if (route === '/alerts') return 'alerts.json'
  if (route === '/entities') return 'entities.json'
  if (route === '/entities/matrix') return 'entities-matrix.json'
  if (route.startsWith('/entities/')) return `entities/${decodeURIComponent(route.slice('/entities/'.length))}.json`
  if (route === '/geo-flows') return 'geo-flows.json'
  if (route.startsWith('/wallet/') && route.endsWith('/dossier')) {
    const addr = decodeURIComponent(route.slice('/wallet/'.length, -'/dossier'.length))
    return `wallets/${addr}.json`
  }
  throw new ApiError(`No static snapshot mapping for ${path}${query ? `?${query}` : ''}`, 0)
}

async function request<T>(path: string): Promise<T> {
  const url = STATIC_DEMO
    ? `${import.meta.env.BASE_URL}demo-data/${staticSnapshotFile(path)}`
    : `${BASE}${path}`

  let res: Response
  try {
    res = await fetch(url)
  } catch {
    // fetch only throws for a network-level failure, which here almost
    // always means the backend simply isn't running.
    throw new ApiError(
      STATIC_DEMO
        ? `Demo snapshot file missing: ${url}`
        : `Cannot reach the API at ${BASE}. Is the backend running?`,
      0,
    )
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* no JSON body; the status line will have to do */
    }
    throw new ApiError(detail, res.status)
  }
  return res.json()
}

// Snapshot of the moment a page of alerts was fetched. WalletAlert carries
// no per-alert timestamp of its own - all alerts come from one ingest batch
// - so this is honestly "as of this refresh", not a fabricated distinct
// per-wallet flagged time the way the old mock data invented one.
function nowStamp(): string {
  return new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
}

interface BackendAlert {
  wallet_address: string
  risk_score: number
  confidence: number
  severity: string
  top_reasons: string[]
}

export const api = {
  getAlerts: async (): Promise<AlertItem[]> => {
    const alerts = await request<BackendAlert[]>('/alerts?limit=500')
    const stamp = nowStamp()
    return alerts.map((a) => ({
      wallet_id: a.wallet_address,
      address: a.wallet_address,
      risk_score: Math.round(a.risk_score * 100),
      confidence: Math.round(a.confidence * 100),
      severity: a.severity.toUpperCase() as Severity,
      reason: a.top_reasons[0] ?? 'No reason recorded',
      flagged_at: stamp,
    }))
  },

  getActors: (): Promise<Actor[]> => request('/entities'),

  getActorDetail: (id: string): Promise<ActorDetail> =>
    request(`/entities/${encodeURIComponent(id)}`),

  getWallet: (address: string): Promise<WalletDetail> =>
    request(`/wallet/${encodeURIComponent(address)}/dossier`),

  getGeoFlows: (): Promise<GeoFlow[]> => request('/geo-flows'),

  getActorMatrix: (): Promise<{ actor_ids: string[]; matrix: number[][] }> =>
    request('/entities/matrix'),
}
