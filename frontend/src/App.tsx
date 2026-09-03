import {
  useState, useEffect, useLayoutEffect, useRef,
  forwardRef, Fragment, useCallback,
} from 'react'
import {
  api, ACTOR_MATRIX,
  type Actor, type ActorDetail, type WalletDetail,
  type AlertItem, type GeoFlow, type TrailHop, type Severity,
} from './mock'
import { GoogleGeoMap } from './GoogleGeoMap'


// ─── Design tokens (JS mirrors of CSS vars) ───────────────────────────────
const C = {
  bg: '#0f1114', p1: '#171a1f', p2: '#1d2128', p3: '#252b34',
  bd: '#262d38', bd2: '#323b47',
  t0: '#dde7f2', t1: '#a8b8ca', t2: '#6a7f96', t3: '#3f5162',
  rc: '#c9512a', rh: '#b87c22', rm: '#6e8faa', rl: '#3e7a60',
  rcBg: 'rgba(201,81,42,0.10)', rhBg: 'rgba(184,124,34,0.10)',
  rmBg: 'rgba(110,143,170,0.10)', rlBg: 'rgba(62,122,96,0.10)',
  sans: "'Plus Jakarta Sans', system-ui, sans-serif",
  mono: "'DM Mono','Menlo',monospace",
}

const SEV: Record<Severity, { color: string; bg: string; rank: number }> = {
  CRITICAL: { color: C.rc, bg: C.rcBg, rank: 4 },
  HIGH:     { color: C.rh, bg: C.rhBg, rank: 3 },
  MEDIUM:   { color: C.rm, bg: C.rmBg, rank: 2 },
  LOW:      { color: C.rl, bg: C.rlBg, rank: 1 },
}

function riskColor(score: number): string {
  return score >= 80 ? C.rc : score >= 60 ? C.rh : score >= 40 ? C.rm : C.rl
}

// ─── Navigation types ─────────────────────────────────────────────────────
type TopTab = 'board' | 'heatmap' | 'geo'

// ─── Utility: simple string hash (for glyphs) ─────────────────────────────
function hashCode(s: string): number {
  return s.split('').reduce((a, c) => ((a * 31 + c.charCodeAt(0)) >>> 0), 0)
}

// ─── Atoms ────────────────────────────────────────────────────────────────
function SevBadge({ sev }: { sev: Severity }) {
  return (
    <span style={{
      fontFamily: C.mono, fontSize: '9.5px', fontWeight: 500,
      color: SEV[sev].color, background: SEV[sev].bg,
      border: `1px solid ${SEV[sev].color}28`,
      padding: '1px 6px', borderRadius: '2px', letterSpacing: '0.04em', whiteSpace: 'nowrap',
    }}>
      {sev}
    </span>
  )
}

function ScorePip({ score, size = 13 }: { score: number; size?: number }) {
  const c = riskColor(score)
  return (
    <span style={{ fontFamily: C.mono, fontSize: `${size}px`, fontWeight: 600, color: c }}>
      {score}
    </span>
  )
}

function Mono({ children, size = 12, color = C.t1 }: { children: React.ReactNode; size?: number; color?: string }) {
  return (
    <span style={{ fontFamily: C.mono, fontSize: `${size}px`, color, letterSpacing: '0.02em' }}>
      {children}
    </span>
  )
}

/** Deterministic 3×3 dot-matrix glyph — unique per actor_id, no stock icons */
function ActorGlyph({ id, score }: { id: string; score: number }) {
  const h = hashCode(id)
  const cells = Array.from({ length: 9 }, (_, i) => !!(h & (1 << i)))
  const col = riskColor(score)
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" style={{ flexShrink: 0 }}>
      {cells.map((on, i) => (
        <rect
          key={i}
          x={(i % 3) * 10 + 0.5} y={Math.floor(i / 3) * 10 + 0.5}
          width="8" height="8" rx="1"
          fill={on ? col : C.p3}
          opacity={on ? 1 : 0.5}
        />
      ))}
    </svg>
  )
}

/** Inline SVG sparkline — no chart library */
function Sparkline({ data }: { data: number[] }) {
  const W = 280, H = 56
  const max = Math.max(...data), min = Math.min(...data), rng = max - min || 1
  const pts = data.map((v, i) => ({
    x: (i / (data.length - 1)) * W,
    y: H - 4 - ((v - min) / rng) * (H - 10),
  }))
  let d = `M ${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`
  for (let i = 1; i < pts.length; i++) {
    const mx = (pts[i-1].x + pts[i].x) / 2
    d += ` C ${mx},${pts[i-1].y.toFixed(1)} ${mx},${pts[i].y.toFixed(1)} ${pts[i].x.toFixed(1)},${pts[i].y.toFixed(1)}`
  }
  const area = `${d} L ${W},${H} L 0,${H} Z`
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block', overflow: 'visible' }}>
      <path d={area} fill={C.rc} fillOpacity="0.08" />
      <path d={d} fill="none" stroke={C.rc} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pts[pts.length-1].x} cy={pts[pts.length-1].y} r="2.5" fill={C.rc} />
    </svg>
  )
}

function Divider() {
  return <div style={{ height: '1px', background: C.bd, margin: '0' }} />
}

// ─── Actor Card (forwarded ref for SVG line measurement) ──────────────────
interface ActorCardProps {
  actor: Actor
  selected: boolean
  onClick: () => void
  ref?: React.Ref<HTMLDivElement>
}
const ActorCard = forwardRef<HTMLDivElement, ActorCardProps>(
  ({ actor, selected, onClick }, ref) => {
    const bdr = selected ? `1px solid ${riskColor(actor.aggregate_risk_score)}60` : `1px solid ${C.bd}`
    return (
      <div
        ref={ref}
        onClick={onClick}
        style={{
          background: selected ? C.p2 : C.p1,
          border: bdr,
          borderRadius: '4px',
          padding: '16px 18px',
          cursor: 'pointer',
          transition: 'background 0.15s, border-color 0.15s',
          display: 'flex', flexDirection: 'column', gap: '10px',
          position: 'relative',
        }}
      >
        {/* Selected indicator */}
        {selected && (
          <div style={{
            position: 'absolute', left: 0, top: 0, bottom: 0,
            width: '3px', background: riskColor(actor.aggregate_risk_score),
            borderRadius: '4px 0 0 4px',
          }} />
        )}

        {/* Header row */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
          <ActorGlyph id={actor.actor_id} score={actor.aggregate_risk_score} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '3px' }}>
              <Mono size={11} color={C.t2}>{actor.actor_id}</Mono>
              <SevBadge sev={actor.aggregate_risk_score >= 80 ? 'CRITICAL' : actor.aggregate_risk_score >= 60 ? 'HIGH' : actor.aggregate_risk_score >= 40 ? 'MEDIUM' : 'LOW'} />
            </div>
            <div style={{ display: 'flex', gap: '12px', alignItems: 'baseline' }}>
              <ScorePip score={actor.aggregate_risk_score} size={20} />
              <span style={{ fontFamily: C.mono, fontSize: '11px', color: C.t2 }}>
                {actor.confidence}% conf
              </span>
            </div>
          </div>
        </div>

        {/* Summary */}
        <p style={{ fontSize: '12px', color: C.t1, lineHeight: 1.55, margin: 0 }}>
          {actor.short_summary}
        </p>

        {/* Footer */}
        <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
          <Mono size={10} color={C.t3}>{actor.member_wallet_ids.length} wallet{actor.member_wallet_ids.length !== 1 ? 's' : ''}</Mono>
          {actor.connected_actor_ids.length > 0 && (
            <>
              <span style={{ color: C.bd2, fontSize: '9px' }}>·</span>
              <Mono size={10} color={C.t3}>{actor.connected_actor_ids.length} link{actor.connected_actor_ids.length !== 1 ? 's' : ''}</Mono>
            </>
          )}
        </div>
      </div>
    )
  }
)

// ─── Actor grid with SVG connection overlay ───────────────────────────────
function ActorGrid({
  actors, selectedId, onSelect,
}: {
  actors: Actor[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const [lines, setLines] = useState<Array<{ x1: number; y1: number; x2: number; y2: number }>>([])

  const measureLines = useCallback(() => {
    if (!containerRef.current) return
    const box = containerRef.current.getBoundingClientRect()
    const seen = new Set<string>()
    const next: typeof lines = []

    for (const actor of actors) {
      for (const cid of actor.connected_actor_ids) {
        const key = [actor.actor_id, cid].sort().join('|')
        if (seen.has(key)) continue
        seen.add(key)
        const a = cardRefs.current.get(actor.actor_id)
        const b = cardRefs.current.get(cid)
        if (!a || !b) continue
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect()
        next.push({
          x1: ra.left - box.left + ra.width / 2,
          y1: ra.top  - box.top  + ra.height / 2,
          x2: rb.left - box.left + rb.width / 2,
          y2: rb.top  - box.top  + rb.height / 2,
        })
      }
    }
    setLines(next)
  }, [actors])

  useLayoutEffect(() => { measureLines() }, [actors, measureLines])

  useEffect(() => {
    const obs = new ResizeObserver(measureLines)
    if (containerRef.current) obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [measureLines])

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      {/* SVG connection overlay */}
      <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', overflow: 'visible', zIndex: 0 }}>
        <defs>
          <marker id="dot" markerWidth="4" markerHeight="4" refX="2" refY="2">
            <circle cx="2" cy="2" r="1.5" fill={C.bd2} />
          </marker>
        </defs>
        {lines.map((l, i) => {
          const mx = (l.x1 + l.x2) / 2
          const my = (l.y1 + l.y2) / 2 - Math.abs(l.x2 - l.x1) * 0.15
          return (
            <path
              key={i}
              d={`M ${l.x1} ${l.y1} Q ${mx} ${my} ${l.x2} ${l.y2}`}
              stroke={C.bd2} strokeWidth="1" fill="none" strokeDasharray="4 4"
              markerEnd="url(#dot)"
            />
          )
        })}
      </svg>

      {/* Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '14px', position: 'relative', zIndex: 1 }}>
        {actors.map(a => (
          <ActorCard
            key={a.actor_id}
            ref={el => { el ? cardRefs.current.set(a.actor_id, el) : cardRefs.current.delete(a.actor_id) }}
            actor={a}
            selected={a.actor_id === selectedId}
            onClick={() => onSelect(a.actor_id === selectedId ? '' : a.actor_id)}
          />
        ))}
      </div>
    </div>
  )
}

// ─── Actor Detail Panel ───────────────────────────────────────────────────
function ActorDetailPanel({
  detail, onClose, onOpenWallet,
}: {
  detail: ActorDetail | null
  onClose: () => void
  onOpenWallet: (walletId: string) => void
}) {
  if (!detail) return null
  const sevColor = riskColor(detail.aggregate_risk_score)

  return (
    <div
      className="slide-right"
      style={{
        width: '360px', flexShrink: 0, background: C.p1,
        borderLeft: `1px solid ${C.bd}`,
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div style={{ padding: '16px 20px', borderBottom: `1px solid ${C.bd}`, display: 'flex', alignItems: 'center', gap: '10px' }}>
        <ActorGlyph id={detail.actor_id} score={detail.aggregate_risk_score} />
        <div style={{ flex: 1 }}>
          <Mono size={10} color={C.t2}>{detail.actor_id}</Mono>
          <div style={{ display: 'flex', gap: '10px', alignItems: 'baseline', marginTop: '2px' }}>
            <ScorePip score={detail.aggregate_risk_score} size={18} />
            <span style={{ fontFamily: C.mono, fontSize: '11px', color: C.t2 }}>{detail.confidence}% confidence</span>
          </div>
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: C.t2, fontSize: '16px', padding: '4px 6px', lineHeight: 1 }}>×</button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {/* Top reasons */}
        <div>
          <div style={{ fontSize: '10px', fontWeight: 600, color: C.t2, letterSpacing: '0.08em', marginBottom: '10px' }}>RISK SIGNALS</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {detail.top_reasons.map((r, i) => (
              <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                <div style={{ width: '3px', height: '3px', borderRadius: '50%', background: sevColor, marginTop: '6px', flexShrink: 0 }} />
                <span style={{ fontSize: '12px', color: C.t1, lineHeight: 1.55 }}>{r}</span>
              </div>
            ))}
          </div>
        </div>

        <Divider />

        {/* Actor connections */}
        {detail.actor_connections.length > 0 && (
          <div>
            <div style={{ fontSize: '10px', fontWeight: 600, color: C.t2, letterSpacing: '0.08em', marginBottom: '10px' }}>ACTOR LINKS</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {detail.actor_connections.map(conn => (
                <div key={conn.target_actor_id} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '7px 10px', background: C.p2, borderRadius: '3px',
                  border: `1px solid ${C.bd}`,
                }}>
                  <Mono size={11} color={C.t1}>{conn.target_actor_id}</Mono>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <Mono size={10} color={C.t2}>{conn.amount_btc.toFixed(2)} BTC</Mono>
                    <span style={{ fontSize: '10px', color: C.t3 }}>{conn.link_type.replace(/_/g, ' ')}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <Divider />

        {/* Member wallets */}
        <div>
          <div style={{ fontSize: '10px', fontWeight: 600, color: C.t2, letterSpacing: '0.08em', marginBottom: '10px' }}>
            MEMBER WALLETS ({detail.member_wallet_ids.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
            {detail.member_wallet_ids.map(wid => (
              <button
                key={wid}
                onClick={() => onOpenWallet(wid)}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '8px 10px', background: C.p2, border: `1px solid ${C.bd}`,
                  borderRadius: '3px', textAlign: 'left', width: '100%',
                }}
              >
                <Mono size={11} color={C.t0}>{wid}</Mono>
                <span style={{ fontSize: '10px', color: C.t2 }}>Open dossier →</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Alert List (flat table mode inside Board) ────────────────────────────
function AlertList({ alerts, onOpenWallet }: { alerts: AlertItem[]; onOpenWallet: (id: string) => void }) {
  const [sortKey, setSortKey] = useState<'risk_score' | 'confidence' | 'severity'>('risk_score')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const sort = (k: typeof sortKey) => {
    if (k === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(k); setSortDir('desc') }
  }

  const sorted = [...alerts].sort((a, b) => {
    let va = 0, vb = 0
    if (sortKey === 'risk_score') { va = a.risk_score; vb = b.risk_score }
    else if (sortKey === 'confidence') { va = a.confidence; vb = b.confidence }
    else { va = SEV[a.severity].rank; vb = SEV[b.severity].rank }
    return sortDir === 'desc' ? vb - va : va - vb
  })

  const TH = ({ label, k }: { label: string; k?: typeof sortKey }) => (
    <th onClick={k ? () => sort(k) : undefined} style={{
      padding: '9px 12px', textAlign: 'left', fontSize: '10px', fontWeight: 600,
      fontFamily: C.mono, letterSpacing: '0.06em',
      color: k && sortKey === k ? C.t0 : C.t2,
      borderBottom: `1px solid ${C.bd}`, cursor: k ? 'pointer' : 'default',
      whiteSpace: 'nowrap', userSelect: 'none', background: C.p1,
    }}>
      {label}{k && sortKey === k ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  )

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead style={{ position: 'sticky', top: 0, zIndex: 5 }}>
        <tr>
          <th style={{ width: '3px', background: C.p1, borderBottom: `1px solid ${C.bd}` }} />
          <TH label="ADDRESS" />
          <TH label="SCORE" k="risk_score" />
          <TH label="CONF." k="confidence" />
          <TH label="SEVERITY" k="severity" />
          <TH label="FLAGGED AT" />
          <th style={{ padding: '9px 12px', textAlign: 'left', fontSize: '10px', fontFamily: C.mono, letterSpacing: '0.06em', color: C.t2, borderBottom: `1px solid ${C.bd}`, background: C.p1, width: '100%' }}>REASON</th>
          <th style={{ background: C.p1, borderBottom: `1px solid ${C.bd}`, width: '72px' }} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((a, idx) => (
          <tr key={a.wallet_id} style={{ background: idx % 2 === 1 ? C.p2 : 'transparent' }}>
            <td style={{ padding: 0, background: SEV[a.severity].color, width: '3px' }} />
            <td style={{ padding: '10px 12px' }}><Mono>{a.address}</Mono></td>
            <td style={{ padding: '10px 12px' }}><ScorePip score={a.risk_score} /></td>
            <td style={{ padding: '10px 12px' }}><Mono size={11} color={C.t1}>{a.confidence}%</Mono></td>
            <td style={{ padding: '10px 12px', whiteSpace: 'nowrap' }}><SevBadge sev={a.severity} /></td>
            <td style={{ padding: '10px 12px' }}><Mono size={10} color={C.t2}>{a.flagged_at}</Mono></td>
            <td style={{ padding: '10px 12px', fontSize: '12px', color: C.t1 }}>{a.reason}</td>
            <td style={{ padding: '10px 12px' }}>
              <button
                onClick={() => onOpenWallet(a.wallet_id)}
                style={{
                  fontSize: '11px', fontFamily: C.mono, color: C.t1,
                  background: C.p3, border: `1px solid ${C.bd2}`,
                  padding: '3px 9px', borderRadius: '2px',
                }}
              >
                Open
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ─── Investigation Board ──────────────────────────────────────────────────
function InvestigationBoard({ onOpenWallet }: { onOpenWallet: (id: string) => void }) {
  const [actors, setActors] = useState<Actor[]>([])
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [selectedActorId, setSelectedActorId] = useState<string | null>(null)
  const [actorDetail, setActorDetail] = useState<ActorDetail | null>(null)
  const [viewMode, setViewMode] = useState<'cards' | 'list'>('cards')

  useEffect(() => {
    api.getActors().then(setActors)
    api.getAlerts().then(setAlerts)
  }, [])

  useEffect(() => {
    if (!selectedActorId) { setActorDetail(null); return }
    api.getActorDetail(selectedActorId).then(setActorDetail)
  }, [selectedActorId])

  const handleSelect = (id: string) => {
    setSelectedActorId(id || null)
  }

  const counts = {
    CRITICAL: actors.filter(a => a.aggregate_risk_score >= 80).length,
    HIGH:     actors.filter(a => a.aggregate_risk_score >= 60 && a.aggregate_risk_score < 80).length,
    MEDIUM:   actors.filter(a => a.aggregate_risk_score >= 40 && a.aggregate_risk_score < 60).length,
    LOW:      actors.filter(a => a.aggregate_risk_score < 40).length,
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Sub-toolbar */}
      <div style={{
        padding: '10px 24px', borderBottom: `1px solid ${C.bd}`, background: C.p1,
        display: 'flex', alignItems: 'center', gap: '24px', flexShrink: 0,
      }}>
        {/* View toggle */}
        <div style={{ display: 'flex', background: C.p2, borderRadius: '4px', border: `1px solid ${C.bd}`, overflow: 'hidden' }}>
          {(['cards', 'list'] as const).map(m => (
            <button key={m} onClick={() => setViewMode(m)} style={{
              padding: '5px 14px', fontSize: '12px', fontWeight: 500,
              color: viewMode === m ? C.t0 : C.t2,
              background: viewMode === m ? C.p3 : 'none',
              border: 'none', borderRadius: '3px',
            }}>
              {m === 'cards' ? 'Actor cards' : 'Alert list'}
            </button>
          ))}
        </div>

        {/* Severity summary pills */}
        <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as Severity[]).map(s => (
            <span key={s} style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '12px', fontFamily: C.mono }}>
              <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: SEV[s].color, display: 'inline-block' }} />
              <span style={{ color: SEV[s].color, fontWeight: 600 }}>{counts[s]}</span>
              <span style={{ color: C.t2 }}>{s}</span>
            </span>
          ))}
        </div>

        <div style={{ marginLeft: 'auto' }}>
          <Mono size={10} color={C.t3}>Last refresh: 2026-08-29 14:32 UTC</Mono>
        </div>
      </div>

      {/* Main content area */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Left: cards or list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: viewMode === 'cards' ? '24px' : '0' }}>
          {viewMode === 'cards' ? (
            actors.length === 0 ? (
              <div style={{ color: C.t2, fontSize: '13px', padding: '40px', textAlign: 'center' }}>Loading actors…</div>
            ) : (
              <ActorGrid actors={actors} selectedId={selectedActorId} onSelect={handleSelect} />
            )
          ) : (
            <AlertList alerts={alerts} onOpenWallet={onOpenWallet} />
          )}
        </div>

        {/* Right: actor detail panel */}
        {selectedActorId && viewMode === 'cards' && (
          <ActorDetailPanel
            detail={actorDetail}
            onClose={() => setSelectedActorId(null)}
            onOpenWallet={onOpenWallet}
          />
        )}
      </div>
    </div>
  )
}

// ─── Wallet Dossier ───────────────────────────────────────────────────────
function WalletDossier({
  walletId, onClose, onOpenTrail,
}: {
  walletId: string
  onClose: () => void
  onOpenTrail: (hops: TrailHop[], origin: WalletDetail) => void
}) {
  const [wallet, setWallet] = useState<WalletDetail | null>(null)
  const [tab, setTab] = useState<'narrative' | 'features'>('narrative')
  const [selectedChip, setSelectedChip] = useState<string | null>(null)

  useEffect(() => {
    setWallet(null)
    api.getWallet(walletId).then(setWallet)
  }, [walletId])

  if (!wallet) {
    return (
      <div className="fade-in" style={{
        position: 'absolute', inset: 0, background: C.bg, zIndex: 20,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Mono color={C.t2}>Loading…</Mono>
      </div>
    )
  }

  const sevColor = SEV[wallet.severity].color

  return (
    <div className="fade-in" style={{
      position: 'absolute', inset: 0, background: C.bg, zIndex: 20,
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        background: C.p1, borderBottom: `1px solid ${C.bd}`,
        padding: '14px 28px', display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0,
      }}>
        <div style={{ width: '3px', height: '36px', background: sevColor, borderRadius: '2px' }} />
        <div style={{ flex: 1 }}>
          <Mono size={9} color={C.t2}>WALLET DOSSIER</Mono>
          <div style={{ fontFamily: C.mono, fontSize: '13px', color: C.t0, wordBreak: 'break-all', marginTop: '3px' }}>
            {wallet.address_full}
          </div>
        </div>
        <button onClick={onClose} style={{ background: 'none', border: `1px solid ${C.bd}`, color: C.t1, fontSize: '12px', padding: '5px 12px', borderRadius: '3px', fontFamily: C.sans }}>
          Close
        </button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Main column */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px 28px', borderRight: `1px solid ${C.bd}` }}>
          {/* Identity block */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px', marginBottom: '24px' }}>
            {[
              { label: 'RISK SCORE', value: String(wallet.risk_score), c: sevColor },
              { label: 'CONFIDENCE', value: `${wallet.confidence}%`, c: C.t0 },
              { label: 'FIRST SEEN', value: wallet.first_seen, c: C.t1 },
              { label: 'LAST ACTIVE', value: wallet.last_active, c: C.t1 },
              { label: 'TX COUNT', value: String(wallet.tx_count), c: C.t0 },
              { label: 'TOTAL VOLUME', value: `${wallet.total_volume_btc} BTC`, c: C.t0 },
              { label: 'SEVERITY', value: wallet.severity, c: sevColor },
              { label: 'STATUS', value: 'UNDER REVIEW', c: C.rm },
            ].map(f => (
              <div key={f.label} style={{ padding: '10px 12px', background: C.p1, border: `1px solid ${C.bd}`, borderRadius: '3px' }}>
                <div style={{ fontFamily: C.mono, fontSize: '8.5px', color: C.t2, letterSpacing: '0.08em', marginBottom: '5px' }}>{f.label}</div>
                <div style={{ fontFamily: C.mono, fontSize: '12px', fontWeight: 500, color: f.c }}>{f.value}</div>
              </div>
            ))}
          </div>

          {/* AI explanation */}
          <div style={{ background: C.p1, border: `1px solid ${C.bd}`, borderRadius: '4px', padding: '20px 22px', marginBottom: '20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', borderBottom: `1px solid ${C.bd}`, marginBottom: '16px', paddingBottom: '0' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: C.t0, flex: 1, paddingBottom: '12px' }}>AI Risk Explanation</span>
              {(['narrative', 'features'] as const).map(t => (
                <button key={t} onClick={() => setTab(t)} style={{
                  fontSize: '11px', fontWeight: tab === t ? 600 : 400,
                  color: tab === t ? C.t0 : C.t2,
                  background: 'none', border: 'none',
                  borderBottom: tab === t ? `1.5px solid ${C.t0}` : '1.5px solid transparent',
                  padding: '0 0 10px 20px',
                }}>
                  {t === 'narrative' ? 'Narrative' : 'Feature breakdown'}
                </button>
              ))}
            </div>

            {tab === 'narrative' && (
              <div style={{ fontSize: '13px', lineHeight: 1.75, color: C.t1, whiteSpace: 'pre-line', maxWidth: '620px' }}>
                {wallet.ai_narrative}
              </div>
            )}
            {tab === 'features' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '520px' }}>
                {wallet.contributing_features.map((f, i) => {
                  const pct = (f.raw / f.max) * 100
                  const col = pct > 75 ? C.rc : pct > 50 ? C.rh : C.rm
                  return (
                    <div key={i}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
                        <span style={{ fontSize: '12px', color: C.t1 }}>{f.name}</span>
                        <Mono size={11} color={col}>{f.raw}{f.unit}</Mono>
                      </div>
                      <div style={{ height: '4px', background: C.p3, borderRadius: '2px', overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: col, borderRadius: '2px', transition: 'width 0.4s ease' }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <button
            onClick={() => onOpenTrail(wallet.trail, wallet)}
            style={{
              fontSize: '13px', fontWeight: 500, fontFamily: C.sans,
              color: C.t0, background: C.p2, border: `1px solid ${C.bd2}`,
              padding: '8px 18px', borderRadius: '3px',
            }}
          >
            View money trail ({wallet.trail.length} hops)
          </button>
        </div>

        {/* Sidebar */}
        <div style={{ width: '280px', flexShrink: 0, overflowY: 'auto', padding: '24px 20px', background: C.p1 }}>
          {/* Sparkline */}
          <div style={{ marginBottom: '28px' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, color: C.t2, letterSpacing: '0.07em', marginBottom: '12px' }}>TX VELOCITY — 16 WEEKS</div>
            <div style={{ padding: '12px', background: C.p2, border: `1px solid ${C.bd}`, borderRadius: '3px' }}>
              <Sparkline data={wallet.velocity_data} />
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '6px' }}>
                <Mono size={9} color={C.t3}>Apr 2026</Mono>
                <Mono size={9} color={C.rc}>peak {Math.max(...wallet.velocity_data)} tx/wk</Mono>
                <Mono size={9} color={C.t3}>Aug 2026</Mono>
              </div>
            </div>
          </div>

          {/* Connected wallets */}
          <div>
            <div style={{ fontSize: '10px', fontWeight: 600, color: C.t2, letterSpacing: '0.07em', marginBottom: '12px' }}>
              CONNECTED WALLETS ({wallet.connected_wallets.length})
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
              {wallet.connected_wallets.map(w => (
                <button
                  key={w.address}
                  onClick={() => setSelectedChip(selectedChip === w.address ? null : w.address)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '8px',
                    padding: '7px 10px', textAlign: 'left', width: '100%',
                    background: selectedChip === w.address ? C.p3 : C.p2,
                    border: `1px solid ${selectedChip === w.address ? C.bd2 : C.bd}`,
                    borderLeft: `2px solid ${SEV[w.severity].color}`,
                    borderRadius: '0 3px 3px 0', outline: 'none',
                  }}
                >
                  <Mono size={11} color={C.t0}>{w.address}</Mono>
                  <ScorePip score={w.risk_score} size={11} />
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Money Trail ──────────────────────────────────────────────────────────
function MoneyTrailView({
  hops, origin, onClose,
}: {
  hops: TrailHop[]
  origin: WalletDetail
  onClose: () => void
}) {
  const [revealed, setRevealed] = useState(0)

  useEffect(() => {
    setRevealed(0)
    const timers = hops.map((_, i) => setTimeout(() => setRevealed(r => Math.max(r, i + 1)), i * 820 + 500))
    return () => timers.forEach(clearTimeout)
  }, [hops])

  return (
    <div className="fade-in" style={{
      position: 'absolute', inset: 0, background: C.bg, zIndex: 30,
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{ background: C.p1, borderBottom: `1px solid ${C.bd}`, padding: '14px 28px', display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0 }}>
        <div style={{ flex: 1 }}>
          <Mono size={9} color={C.t2}>MONEY TRAIL</Mono>
          <div style={{ fontSize: '14px', fontWeight: 600, color: C.t0, marginTop: '2px' }}>
            {hops.length + 1}-hop trace · origin {origin.address}
          </div>
        </div>
        <button onClick={onClose} style={{ background: 'none', border: `1px solid ${C.bd}`, color: C.t1, fontSize: '12px', padding: '5px 12px', borderRadius: '3px', fontFamily: C.sans }}>
          Close
        </button>
      </div>

      {/* Hops */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div style={{ maxWidth: '680px', margin: '0 auto', padding: '32px 40px 80px' }}>
          {/* Origin node */}
          <div style={{ background: C.p1, border: `1px solid ${C.bd}`, borderLeft: `3px solid ${SEV[origin.severity].color}`, borderRadius: '0 4px 4px 0', padding: '16px 20px' }}>
            <Mono size={9} color={C.t2}>ORIGIN WALLET</Mono>
            <div style={{ fontFamily: C.mono, fontSize: '13px', color: C.t0, margin: '6px 0 8px', wordBreak: 'break-all' }}>
              {origin.address_full}
            </div>
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
              <ScorePip score={origin.risk_score} />
              <SevBadge sev={origin.severity} />
            </div>
          </div>

          {/* Hop sequence */}
          {hops.map((hop, i) => (
            <Fragment key={hop.step}>
              {i < revealed && (
                <div className="line-grow" style={{ margin: '0 0 0 26px', padding: '12px 0 12px 22px', borderLeft: `1px solid ${C.bd2}` }}>
                  <div style={{ display: 'inline-block', padding: '9px 14px', background: C.p1, border: `1px solid ${C.bd}`, borderRadius: '4px' }}>
                    <div style={{ display: 'flex', gap: '14px', alignItems: 'baseline', marginBottom: '4px' }}>
                      <Mono size={14} color={C.t0}>{hop.amount_btc.toFixed(4)} BTC</Mono>
                      <Mono size={11} color={C.t2}>${hop.amount_usd.toLocaleString()}</Mono>
                    </div>
                    <Mono size={10} color={C.t3}>{hop.timestamp}</Mono>
                    <br />
                    <Mono size={10} color={C.t3}>TX {hop.tx_hash}</Mono>
                  </div>
                </div>
              )}
              {i < revealed && (
                <div className="hop-in" style={{ background: C.p1, border: `1px solid ${C.bd}`, borderLeft: `3px solid ${SEV[hop.to_severity].color}`, borderRadius: '0 4px 4px 0', padding: '16px 20px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                    <Mono size={9} color={C.t2}>HOP {hop.step}</Mono>
                    <span style={{ color: C.bd2, fontSize: '9px' }}>—</span>
                    <span style={{ fontSize: '12px', color: C.t1 }}>{hop.to_label}</span>
                  </div>
                  <div style={{ fontFamily: C.mono, fontSize: '13px', color: C.t0, marginBottom: '8px' }}>{hop.to_wallet}</div>
                  <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                    <ScorePip score={hop.to_score} />
                    <SevBadge sev={hop.to_severity} />
                  </div>
                </div>
              )}
            </Fragment>
          ))}

          {revealed < hops.length && (
            <div style={{ margin: '0 0 0 26px', padding: '16px 0 0 22px', borderLeft: `1px solid ${C.bd2}` }}>
              <Mono size={11} color={C.t3}>Tracing next hop…</Mono>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Heatmap view (actor × actor intensity) ───────────────────────────────
function HeatmapView() {
  const [actors, setActors] = useState<Actor[]>([])
  const [hovered, setHovered] = useState<{ r: number; c: number } | null>(null)
  const CW = 64, CH = 30

  useEffect(() => { api.getActors().then(setActors) }, [])

  function cellColor(val: number): string {
    if (val === 0) return 'transparent'
    const t = val / 100
    const r = Math.round(40  + t * (201 - 40))
    const g = Math.round(80  + t * (81  - 80))
    const b = Math.round(100 + t * (42  - 100))
    return `rgba(${r},${g},${b},${0.15 + t * 0.72})`
  }

  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '32px 36px', background: C.bg }}>
      <div style={{ marginBottom: '24px' }}>
        <Mono size={9} color={C.t2}>RELATIONSHIP HEATMAP</Mono>
        <div style={{ fontSize: '20px', fontWeight: 700, color: C.t0, marginTop: '4px', letterSpacing: '-0.01em' }}>
          Actor-to-actor transaction intensity
        </div>
        <div style={{ fontSize: '13px', color: C.t2, marginTop: '6px', maxWidth: '520px', lineHeight: 1.55 }}>
          Cell shading indicates transaction volume between actor pairs, weighted by combined risk score.
        </div>
      </div>

      <div style={{ overflowX: 'auto', paddingBottom: '16px' }}>
        <div style={{ display: 'inline-block', background: C.p1, border: `1px solid ${C.bd}`, borderRadius: '4px', padding: '20px' }}>
          {/* Column headers */}
          <div style={{ display: 'flex', marginLeft: '92px', marginBottom: '4px' }}>
            {actors.map((a, c) => (
              <div key={c} style={{
                width: `${CW}px`, flexShrink: 0, textAlign: 'center',
                fontFamily: C.mono, fontSize: '8.5px', color: C.t2,
                paddingBottom: '6px', borderBottom: `1px solid ${C.bd}`,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', padding: '0 2px 6px',
              }}>
                {a.actor_id}
              </div>
            ))}
          </div>

          {/* Rows */}
          {actors.map((rowA, r) => (
            <div key={r} style={{ display: 'flex', alignItems: 'center' }}>
              <div style={{ width: '92px', flexShrink: 0, paddingRight: '12px', textAlign: 'right', fontFamily: C.mono, fontSize: '9px', color: C.t1, whiteSpace: 'nowrap' }}>
                {rowA.actor_id}
              </div>
              {actors.map((_, c) => {
                const val = r < ACTOR_MATRIX.length && c < ACTOR_MATRIX[r].length ? ACTOR_MATRIX[r][c] : 0
                const isH = hovered?.r === r && hovered?.c === c
                const isDiag = r === c
                return (
                  <div
                    key={c}
                    onMouseEnter={() => !isDiag && val > 0 && setHovered({ r, c })}
                    onMouseLeave={() => setHovered(null)}
                    style={{
                      width: `${CW}px`, height: `${CH}px`, flexShrink: 0,
                      background: isDiag ? C.p2 : isH ? `rgba(201,81,42,0.45)` : cellColor(val),
                      border: `1px solid ${C.bd}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      cursor: val > 0 && !isDiag ? 'pointer' : 'default',
                      transition: 'background 0.12s',
                    }}
                    title={val > 0 && !isDiag ? `${rowA.actor_id} ↔ ${actors[c].actor_id}: ${val}` : undefined}
                  >
                    {val > 0 && !isDiag && (
                      <span style={{ fontFamily: C.mono, fontSize: '9px', color: val > 50 ? 'rgba(255,255,255,0.8)' : C.t2, fontWeight: val > 50 ? 600 : 400 }}>
                        {val}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          ))}

          {/* Legend */}
          <div style={{ marginTop: '16px', marginLeft: '92px', display: 'flex', alignItems: 'center', gap: '5px' }}>
            <Mono size={9} color={C.t3}>Intensity:</Mono>
            {[0, 20, 40, 60, 80, 100].map(v => (
              <div key={v} style={{ width: '28px', height: '12px', background: cellColor(v), border: `1px solid ${C.bd}`, borderRadius: '1px' }} />
            ))}
            <Mono size={9} color={C.t3}>Low → High</Mono>
          </div>
        </div>
      </div>

      {hovered && ACTOR_MATRIX[hovered.r]?.[hovered.c] > 0 && actors[hovered.r] && actors[hovered.c] && (
        <div style={{ marginTop: '20px', padding: '14px 18px', background: C.p1, border: `1px solid ${C.bd}`, borderRadius: '4px', display: 'inline-block' }}>
          <Mono size={9} color={C.t2}>PAIR DETAIL</Mono>
          <div style={{ fontFamily: C.mono, fontSize: '13px', color: C.t0, margin: '6px 0 6px' }}>
            {actors[hovered.r].actor_id} ↔ {actors[hovered.c].actor_id}
          </div>
          <span style={{ fontFamily: C.mono, fontSize: '12px', color: C.t1 }}>
            Intensity: <span style={{ color: C.rc }}>{ACTOR_MATRIX[hovered.r][hovered.c]}</span>
          </span>
        </div>
      )}
    </div>
  )
}

// ─── Geographic Flow View — hand-built flat SVG world map ─────────────────
// Equirectangular projection: x = (lon+180)/360*W, y = (90-lat)/180*H
const MAP_W = 1000, MAP_H = 480

function lonLat(lon: number, lat: number): [number, number] {
  return [(lon + 180) / 360 * MAP_W, (90 - lat) / 180 * MAP_H]
}

// Simplified continent polygons (approximate, recognizable at overview scale)
const CONTINENT_PATHS: string[] = [
  // North America
  mkPath([[-168,72],[-100,83],[-55,68],[-53,48],[-58,10],[-82,10],[-105,19],[-125,32],[-148,60]]),
  // South America
  mkPath([[-80,12],[-65,12],[-35,-5],[-35,-55],[-68,-55],[-80,-40]]),
  // Europe (simplified)
  mkPath([[-10,72],[30,72],[42,60],[45,40],[28,36],[10,36],[-5,40],[-10,50]]),
  // Africa
  mkPath([[-18,38],[52,38],[55,12],[50,-10],[40,-35],[15,-35],[15,-30],[-18,10]]),
  // Asia
  mkPath([[30,72],[180,72],[180,10],[130,10],[100,5],[65,10],[45,40],[30,72]]),
  // Australia
  mkPath([[114,-22],[154,-22],[155,-38],[138,-40],[125,-35],[114,-28]]),
  // Greenland
  mkPath([[-45,84],[-20,84],[-20,76],[-44,76]]),
  // Japan (simplified)
  mkPath([[130,32],[132,32],[132,45],[130,45]]),
  // UK (simplified)
  mkPath([[-6,50],[-6,58],[0,58],[0,50]]),
]

function mkPath(pts: [number, number][]): string {
  return pts.map(([lon, lat], i) => {
    const [x, y] = lonLat(lon, lat)
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
  }).join(' ') + ' Z'
}

// Country centroid coordinates [lon, lat]
const COUNTRY_CENTERS: Record<string, [number, number]> = {
  'USA': [-98, 38], 'Canada': [-96, 60], 'Mexico': [-102, 24],
  'Brazil': [-51, -14], 'UK': [-3, 54], 'Germany': [10, 51],
  'France': [2, 46], 'Switzerland': [8, 47], 'Netherlands': [5, 52],
  'Russia': [100, 60], 'China': [105, 35], 'India': [78, 20],
  'Japan': [138, 36], 'South Korea': [128, 37], 'Singapore': [104, 1],
  'UAE': [54, 24], 'Nigeria': [8, 10], 'Australia': [133, -27],
}

function countryXY(name: string): [number, number] | null {
  const c = COUNTRY_CENTERS[name]
  if (!c) return null
  return lonLat(c[0], c[1])
}

function arcPath(x1: number, y1: number, x2: number, y2: number): string {
  const mx = (x1 + x2) / 2
  const dist = Math.sqrt((x2-x1)**2 + (y2-y1)**2)
  const cpy = Math.min(y1, y2) - dist * 0.22 - 10
  return `M ${x1.toFixed(1)} ${y1.toFixed(1)} Q ${mx.toFixed(1)} ${cpy.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`
}

function GeoFlowView() {
  const [flows, setFlows] = useState<GeoFlow[]>([])
  const [hovered, setHovered] = useState<GeoFlow | null>(null)

  useEffect(() => { api.getGeoFlows().then(setFlows) }, [])

  const maxAmount = flows.length ? Math.max(...flows.map(f => f.amount)) : 1

  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '32px 36px', background: C.bg }}>
      <div style={{ marginBottom: '20px' }}>
        <Mono size={9} color={C.t2}>GEOGRAPHIC FLOW</Mono>
        <div style={{ fontSize: '20px', fontWeight: 700, color: C.t0, marginTop: '4px', letterSpacing: '-0.01em' }}>
          Cross-border transaction flows
        </div>
        <div style={{ fontSize: '13px', color: C.t2, marginTop: '6px' }}>
          Arc thickness encodes BTC volume · Arc color encodes risk score
        </div>
      </div>

      {/* Realtime Google Maps API View */}
      <div style={{ background: C.p1, border: `1px solid ${C.bd}`, borderRadius: '4px', padding: '8px' }}>
        <GoogleGeoMap flows={flows} hovered={hovered} setHovered={setHovered} />
      </div>


      {/* Hover tooltip */}
      {hovered && (
        <div style={{ marginTop: '16px', padding: '14px 18px', background: C.p1, border: `1px solid ${C.bd}`, borderRadius: '4px', display: 'inline-flex', gap: '24px', alignItems: 'center' }}>
          <div>
            <Mono size={9} color={C.t2}>FLOW</Mono>
            <div style={{ fontFamily: C.mono, fontSize: '13px', color: C.t0, marginTop: '4px' }}>
              {hovered.from_country} → {hovered.to_country}
            </div>
          </div>
          <div>
            <Mono size={9} color={C.t2}>AMOUNT</Mono>
            <div style={{ fontFamily: C.mono, fontSize: '13px', color: C.t0, marginTop: '4px' }}>{hovered.amount.toFixed(2)} BTC</div>
          </div>
          <div>
            <Mono size={9} color={C.t2}>RISK SCORE</Mono>
            <div style={{ marginTop: '4px' }}><ScorePip score={hovered.risk_score} size={14} /></div>
          </div>
          <SevBadge sev={hovered.risk_score >= 80 ? 'CRITICAL' : hovered.risk_score >= 60 ? 'HIGH' : hovered.risk_score >= 40 ? 'MEDIUM' : 'LOW'} />
        </div>
      )}

      {/* Flow table */}
      <div style={{ marginTop: '24px' }}>
        <Mono size={9} color={C.t2}>ALL FLOWS</Mono>
        <div style={{ marginTop: '10px', background: C.p1, border: `1px solid ${C.bd}`, borderRadius: '4px', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['FROM', 'TO', 'AMOUNT (BTC)', 'RISK SCORE', ''].map(h => (
                  <th key={h} style={{ padding: '8px 14px', textAlign: 'left', fontSize: '9.5px', fontFamily: C.mono, letterSpacing: '0.06em', color: C.t2, borderBottom: `1px solid ${C.bd}` }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...flows].sort((a, b) => b.risk_score - a.risk_score).map((f, i) => (
                <tr
                  key={i}
                  onMouseEnter={() => setHovered(f)}
                  onMouseLeave={() => setHovered(null)}
                  style={{ background: hovered === f ? C.p2 : i % 2 === 1 ? C.p1 : 'transparent', cursor: 'default' }}
                >
                  <td style={{ padding: '9px 14px' }}><Mono size={12}>{f.from_country}</Mono></td>
                  <td style={{ padding: '9px 14px' }}><Mono size={12}>{f.to_country}</Mono></td>
                  <td style={{ padding: '9px 14px' }}><Mono size={12} color={C.t0}>{f.amount.toFixed(2)}</Mono></td>
                  <td style={{ padding: '9px 14px' }}><ScorePip score={f.risk_score} size={12} /></td>
                  <td style={{ padding: '9px 14px' }}><SevBadge sev={f.risk_score >= 80 ? 'CRITICAL' : f.risk_score >= 60 ? 'HIGH' : f.risk_score >= 40 ? 'MEDIUM' : 'LOW'} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ─── App root ─────────────────────────────────────────────────────────────
export default function App() {
  const [tab, setTab] = useState<TopTab>('board')
  const [openWalletId, setOpenWalletId] = useState<string | null>(null)
  const [trailState, setTrailState] = useState<{ hops: TrailHop[]; origin: WalletDetail } | null>(null)

  const TABS: { key: TopTab; label: string }[] = [
    { key: 'board',   label: 'Investigation Board' },
    { key: 'heatmap', label: 'Heatmap' },
    { key: 'geo',     label: 'Geographic' },
  ]

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: C.bg, fontFamily: C.sans, color: C.t0 }}>
      {/* App bar */}
      <header style={{ background: C.p1, borderBottom: `1px solid ${C.bd}`, display: 'flex', alignItems: 'stretch', height: '48px', flexShrink: 0 }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', padding: '0 24px', borderRight: `1px solid ${C.bd}`, gap: '10px' }}>
          {/* Inline logo glyph */}
          <svg width="18" height="18" viewBox="0 0 18 18">
            {[0,1,2,3,4,5,6,7,8].map(i => (
              <rect key={i} x={(i%3)*6+0.5} y={Math.floor(i/3)*6+0.5} width="5" height="5" rx="1"
                fill={[0,2,4,6,8].includes(i) ? C.rc : C.p3} />
            ))}
          </svg>
          <span style={{ fontFamily: C.mono, fontSize: '11px', fontWeight: 500, color: C.t0, letterSpacing: '0.10em' }}>
            CRYPTOTRACE
          </span>
        </div>

        {/* Top tabs */}
        <nav style={{ display: 'flex' }}>
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)} style={{
              fontSize: '13px', fontWeight: tab === t.key ? 600 : 400,
              color: tab === t.key ? C.t0 : C.t2,
              background: 'none', border: 'none', outline: 'none',
              borderBottom: tab === t.key ? `2px solid ${C.rc}` : '2px solid transparent',
              padding: '0 20px', height: '100%', transition: 'color 0.15s',
            }}>
              {t.label}
            </button>
          ))}
        </nav>

        {/* Session info */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', padding: '0 24px', gap: '10px' }}>
          <Mono size={10} color={C.t3}>ANL-2847</Mono>
          <div style={{ width: '1px', height: '12px', background: C.bd }} />
          <Mono size={10} color={C.t3}>2026-08-29</Mono>
        </div>
      </header>

      {/* Main view — relative for overlay anchoring */}
      <main style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {tab === 'board'   && <InvestigationBoard onOpenWallet={setOpenWalletId} />}
        {tab === 'heatmap' && <HeatmapView />}
        {tab === 'geo'     && <GeoFlowView />}

        {/* Wallet Dossier overlay */}
        {openWalletId && (
          <WalletDossier
            walletId={openWalletId}
            onClose={() => setOpenWalletId(null)}
            onOpenTrail={(hops, origin) => setTrailState({ hops, origin })}
          />
        )}

        {/* Money Trail overlay (above dossier) */}
        {trailState && (
          <MoneyTrailView
            hops={trailState.hops}
            origin={trailState.origin}
            onClose={() => setTrailState(null)}
          />
        )}
      </main>
    </div>
  )
}
