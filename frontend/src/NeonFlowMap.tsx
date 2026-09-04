import { useMemo } from 'react'
import { geoNaturalEarth1, geoPath, geoGraticule } from 'd3-geo'
import { feature, mesh } from 'topojson-client'
// Real Natural Earth geometry (public domain, ships with the npm package -
// no API key, no runtime network call, works fully offline). This replaces
// the earlier hand-drawn continent approximations with actual coastlines
// and country borders.
import worldTopo from 'world-atlas/countries-110m.json'
import type { GeoFlow } from './api'
import { neonRiskColor } from './theme'

interface NeonFlowMapProps {
  flows: GeoFlow[]
  hovered: GeoFlow | null
  setHovered: (flow: GeoFlow | null) => void
  onSelect?: (flow: GeoFlow) => void
}

const MAP_W = 1000
const MAP_H = 480

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const world = worldTopo as any
const land = feature(world, world.objects.land) as any
const borders = mesh(world, world.objects.countries, (a: any, b: any) => a !== b) as any

// geoNaturalEarth1: the "proper atlas" projection (rounded poles, gentle
// curvature) real map products use for a world overview, as opposed to the
// stretched-at-the-poles equirectangular box the hand-drawn version used.
const projection = geoNaturalEarth1().fitSize([MAP_W, MAP_H], land)
const pathGen = geoPath(projection)

const LAND_PATH = pathGen(land) ?? ''
const BORDER_PATH = pathGen(borders) ?? ''
const GRATICULE_PATH = pathGen(geoGraticule().step([30, 30])()) ?? ''

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
  const p = projection(c)
  return p ? [p[0], p[1]] : null
}

function arcPath(x1: number, y1: number, x2: number, y2: number): string {
  const mx = (x1 + x2) / 2
  const dist = Math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
  const cpy = Math.min(y1, y2) - dist * 0.22 - 10
  return `M ${x1.toFixed(1)} ${y1.toFixed(1)} Q ${mx.toFixed(1)} ${cpy.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`
}

export function NeonFlowMap({ flows, hovered, setHovered, onSelect }: NeonFlowMapProps) {
  const maxAmount = flows.length ? Math.max(...flows.map(f => f.amount)) : 1

  const involvedCountries = useMemo(() => {
    const set = new Set<string>()
    flows.forEach(f => { set.add(f.from_country); set.add(f.to_country) })
    return Array.from(set)
  }, [flows])

  // render the hovered arc last so it draws on top of the others
  const ordered = useMemo(
    () => flows.map((f, i) => ({ f, i })).sort((a, b) => (a.f === hovered ? 1 : 0) - (b.f === hovered ? 1 : 0)),
    [flows, hovered]
  )

  return (
    <div style={{
      position: 'relative', width: '100%', height: '480px', borderRadius: '4px',
      overflow: 'hidden', border: '1px solid #262d38',
      background: 'radial-gradient(ellipse at 50% 38%, #0e1c22 0%, #0a0e12 55%, #07090b 100%)',
    }}>
      <style>{`
        @keyframes neonDash { to { stroke-dashoffset: -240; } }
        @keyframes livePulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
      `}</style>

      <svg viewBox={`0 0 ${MAP_W} ${MAP_H}`} width="100%" height="100%" style={{ display: 'block' }}>
        <defs>
          <filter id="neonGlow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="3.4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <linearGradient id="continentFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#152230" />
            <stop offset="100%" stopColor="#0e1620" />
          </linearGradient>
        </defs>

        <path d={GRATICULE_PATH} fill="none" stroke="#1c5a66" strokeOpacity={0.14} strokeWidth={0.6} />
        <path d={LAND_PATH} fill="url(#continentFill)" stroke="none" />
        <path d={BORDER_PATH} fill="none" stroke="#2f8a9c" strokeOpacity={0.45} strokeWidth={0.5} />

        {/* flow arcs */}
        {ordered.map(({ f, i }) => {
          const from = countryXY(f.from_country)
          const to = countryXY(f.to_country)
          if (!from || !to) return null
          const [x1, y1] = from
          const [x2, y2] = to
          const d = arcPath(x1, y1, x2, y2)
          const color = neonRiskColor(f.risk_score)
          const isHovered = hovered === f
          const weight = 1 + (f.amount / maxAmount) * 3
          const dur = (2.6 - Math.min(1.6, (f.amount / maxAmount) * 1.6)).toFixed(2)

          return (
            <g key={i} onMouseEnter={() => setHovered(f)} onClick={() => onSelect?.(f)} style={{ cursor: 'pointer' }}>
              {/* wide invisible hit target, easier to hover than the thin line */}
              <path d={d} fill="none" stroke="transparent" strokeWidth={14} />
              {/* base glow */}
              <path
                d={d} fill="none" stroke={color}
                strokeWidth={isHovered ? weight + 2.5 : weight}
                strokeOpacity={isHovered ? 0.95 : 0.45}
                filter="url(#neonGlow)"
              />
              {/* flowing dash line */}
              <path
                d={d} fill="none" stroke={color}
                strokeWidth={isHovered ? 2 : 1.1}
                strokeOpacity={isHovered ? 1 : 0.85}
                strokeLinecap="round"
                strokeDasharray="5 13"
                style={{ animation: `neonDash ${dur}s linear infinite` }}
              />
              {/* traveling transaction packet */}
              <circle r={isHovered ? 3.6 : 2.4} fill={color} filter="url(#neonGlow)">
                <animateMotion dur={`${dur}s`} repeatCount="indefinite" path={d} />
              </circle>
            </g>
          )
        })}

        {/* country markers + labels */}
        {involvedCountries.map(name => {
          const xy = countryXY(name)
          if (!xy) return null
          const [x, y] = xy
          const maxRisk = Math.max(0, ...flows.filter(f => f.from_country === name || f.to_country === name).map(f => f.risk_score))
          const color = neonRiskColor(maxRisk)
          const labelW = 8 + name.length * 5.6
          return (
            <g key={name}>
              <circle cx={x} cy={y} r={4} fill={color} filter="url(#neonGlow)">
                <animate attributeName="r" values="4;7;4" dur="2.4s" repeatCount="indefinite" />
                <animate attributeName="opacity" values="0.9;0.35;0.9" dur="2.4s" repeatCount="indefinite" />
              </circle>
              <circle cx={x} cy={y} r={1.8} fill="#07090b" />
              <rect x={x + 7} y={y - 17} width={labelW} height={13} rx={2} fill="#07090bcc" stroke="#1c2830" strokeWidth={0.5} />
              <text x={x + 7 + labelW / 2} y={y - 7.5} fontSize={9} textAnchor="middle" fontFamily="'DM Mono', monospace" fill="#a8b8ca">{name}</text>
            </g>
          )
        })}
      </svg>

      <div style={{
        position: 'absolute', top: 10, right: 12, display: 'flex', alignItems: 'center', gap: 6,
        fontFamily: "'DM Mono', monospace", fontSize: 10, letterSpacing: '0.08em', color: '#39e08a',
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: '50%', background: '#39e08a',
          boxShadow: '0 0 6px #39e08a', animation: 'livePulse 1.6s ease-in-out infinite',
        }} />
        LIVE
      </div>

      <div style={{
        position: 'absolute', bottom: 10, left: 12, display: 'flex', gap: '14px',
        fontFamily: "'DM Mono', monospace", fontSize: 9.5, letterSpacing: '0.04em', color: '#6a7f96',
      }}>
        {[
          { label: 'CRITICAL', color: neonRiskColor(90) },
          { label: 'HIGH', color: neonRiskColor(65) },
          { label: 'MEDIUM', color: neonRiskColor(45) },
          { label: 'LOW', color: neonRiskColor(10) },
        ].map(({ label, color }) => (
          <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 9, height: 2.5, background: color, boxShadow: `0 0 5px ${color}`, display: 'inline-block', borderRadius: '2px' }} />
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}
