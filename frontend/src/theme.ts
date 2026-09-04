import type { Severity } from './api'

// ─── Design tokens ─────────────────────────────────────────────────────────
// Single source of truth for the dashboard's color palette. Anything that
// needs to render a risk color (badges, score pips, the map) imports from
// here so they can't drift out of sync with each other.
export const C = {
  bg: '#0f1114', p1: '#171a1f', p2: '#1d2128', p3: '#252b34',
  bd: '#262d38', bd2: '#323b47',
  t0: '#dde7f2', t1: '#a8b8ca', t2: '#6a7f96', t3: '#3f5162',
  rc: '#c9512a', rh: '#b87c22', rm: '#6e8faa', rl: '#3e7a60',
  rcBg: 'rgba(201,81,42,0.10)', rhBg: 'rgba(184,124,34,0.10)',
  rmBg: 'rgba(110,143,170,0.10)', rlBg: 'rgba(62,122,96,0.10)',
  sans: "'Plus Jakarta Sans', system-ui, sans-serif",
  mono: "'DM Mono','Menlo',monospace",
}

export const SEV: Record<Severity, { color: string; bg: string; rank: number }> = {
  CRITICAL: { color: C.rc, bg: C.rcBg, rank: 4 },
  HIGH:     { color: C.rh, bg: C.rhBg, rank: 3 },
  MEDIUM:   { color: C.rm, bg: C.rmBg, rank: 2 },
  LOW:      { color: C.rl, bg: C.rlBg, rank: 1 },
}

export function riskColor(score: number): string {
  return score >= 80 ? C.rc : score >= 60 ? C.rh : score >= 40 ? C.rm : C.rl
}

export function severityOf(score: number): Severity {
  return score >= 80 ? 'CRITICAL' : score >= 60 ? 'HIGH' : score >= 40 ? 'MEDIUM' : 'LOW'
}

// Brightened, saturated variants of the same four tiers above - the
// dashboard palette (riskColor()) is deliberately muted for dense tables,
// but a thin glowing line against a near-black map needs more punch to read
// at a glance. Same hue family and same >=80/60/40 cutoffs as riskColor(),
// just lifted in brightness/saturation for the neon map.
export function neonRiskColor(score: number): string {
  if (score >= 80) return '#ff5a3c' // CRITICAL - vivid red-orange (vs C.rc)
  if (score >= 60) return '#ffb347' // HIGH - vivid amber (vs C.rh)
  if (score >= 40) return '#5ec8ff' // MEDIUM - vivid sky blue (vs C.rm)
  return '#39e08a'                  // LOW - vivid green (vs C.rl)
}
