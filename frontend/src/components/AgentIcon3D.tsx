import type { Tone } from './AgentCard'

/** Large 3D-style icons for the swarm cards. Each agent has its own SVG art (gradients + fill).
 *  Motion is a gentle Y-tilt so glyphs stay visible — full spins were going edge-on and disappearing. */

const GRAD: Record<string, { a: string; b: string }> = {
  vision: { a: '#5eead4', b: '#0d9488' },
  procedure: { a: '#67e8f9', b: '#0891b2' },
  surgical: { a: '#7dd3fc', b: '#0284c7' },
  anesthesia: { a: '#f472b6', b: '#be185d' },
  record: { a: '#a5b4fc', b: '#4338ca' },
  safety: { a: '#fcd34d', b: '#d97706' },
  gate: { a: '#fb923c', b: '#c2410c' },
  archivist: { a: '#94a3b8', b: '#475569' },
}

function Defs({ id }: { id: string }) {
  const g = GRAD[id] ?? GRAD.vision
  return (
    <defs>
      <linearGradient id={`ag-fill-${id}`} x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor={g.a} />
        <stop offset="100%" stopColor={g.b} />
      </linearGradient>
      <linearGradient id={`ag-shine-${id}`} x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stopColor="#fff" stopOpacity="0.35" />
        <stop offset="55%" stopColor="#fff" stopOpacity="0" />
      </linearGradient>
      <filter id={`ag-glow-${id}`} x="-40%" y="-40%" width="180%" height="180%">
        <feGaussianBlur stdDeviation="2.5" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
    </defs>
  )
}

function Art({ id }: { id: string }) {
  const fill = `url(#ag-fill-${id})`
  const shine = `url(#ag-shine-${id})`
  const stroke = 'rgba(255,255,255,0.55)'
  switch (id) {
    case 'vision':
      return (
        <>
          <ellipse cx="48" cy="78" rx="28" ry="8" fill="rgba(0,0,0,0.35)" />
          <circle cx="48" cy="44" r="30" fill={fill} stroke={stroke} strokeWidth="1.5" filter={`url(#ag-glow-${id})`} />
          <circle cx="48" cy="44" r="22" fill="none" stroke="rgba(255,255,255,0.25)" strokeWidth="1.2" />
          <circle cx="48" cy="44" r="12" fill="#042f2e" stroke={stroke} strokeWidth="1" />
          <circle cx="44" cy="40" r="4" fill={shine} opacity="0.9" />
          <rect x="38" y="18" width="20" height="8" rx="2" fill={fill} opacity="0.8" />
        </>
      )
    case 'procedure':
      return (
        <>
          <ellipse cx="48" cy="78" rx="26" ry="7" fill="rgba(0,0,0,0.35)" />
          <rect x="22" y="28" width="22" height="16" rx="3" fill={fill} stroke={stroke} strokeWidth="1.2" />
          <rect x="52" y="42" width="22" height="16" rx="3" fill={fill} stroke={stroke} strokeWidth="1.2" />
          <rect x="22" y="58" width="22" height="16" rx="3" fill={fill} stroke={stroke} strokeWidth="1.2" />
          <path d="M44 36h8v20M58 50h-6v16" stroke={stroke} strokeWidth="2" fill="none" />
          <circle cx="72" cy="24" r="6" fill={fill} stroke={stroke} />
          <path d="M69 24h6M72 21v6" stroke="#fff" strokeWidth="1.2" opacity="0.7" />
        </>
      )
    case 'surgical':
      return (
        <>
          <ellipse cx="48" cy="78" rx="26" ry="7" fill="rgba(0,0,0,0.35)" />
          <path d="M20 72 L52 36 L68 22" stroke={fill} strokeWidth="5" strokeLinecap="round" fill="none" filter={`url(#ag-glow-${id})`} />
          <path d="M20 72 L52 36 L68 22" stroke={stroke} strokeWidth="1.5" fill="none" />
          <circle cx="20" cy="72" r="7" fill={fill} stroke={stroke} />
          <path d="M58 32 L74 18 M62 38 L78 28" stroke={stroke} strokeWidth="2" strokeLinecap="round" />
        </>
      )
    case 'anesthesia':
      return (
        <>
          <ellipse cx="48" cy="78" rx="26" ry="7" fill="rgba(0,0,0,0.35)" />
          <path d="M48 76 C24 58 14 44 18 30 C22 18 36 16 48 28 C60 16 74 18 78 30 C82 44 72 58 48 76Z" fill={fill} stroke={stroke} strokeWidth="1.5" filter={`url(#ag-glow-${id})`} />
          <path d="M22 48 H32 L38 36 L48 58 L54 42 L60 48 H74" stroke="#fff" strokeWidth="2.2" fill="none" strokeLinecap="round" strokeLinejoin="round" opacity="0.85" />
        </>
      )
    case 'record':
      return (
        <>
          <ellipse cx="48" cy="78" rx="26" ry="7" fill="rgba(0,0,0,0.35)" />
          <path d="M28 16 H56 L70 30 V80 H28 Z" fill={fill} stroke={stroke} strokeWidth="1.5" filter={`url(#ag-glow-${id})`} />
          <path d="M56 16 V30 H70" fill="none" stroke={stroke} strokeWidth="1.2" />
          <path d="M36 44 H62 M36 54 H62 M36 64 H50" stroke="rgba(255,255,255,0.5)" strokeWidth="1.5" />
          <path d="M58 68 L62 72 L72 60" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" />
        </>
      )
    case 'safety':
      return (
        <>
          <ellipse cx="48" cy="78" rx="26" ry="7" fill="rgba(0,0,0,0.35)" />
          <path d="M48 14 L76 24 V46 C76 62 64 74 48 82 C32 74 20 62 20 46 V24 Z" fill={fill} stroke={stroke} strokeWidth="1.5" filter={`url(#ag-glow-${id})`} />
          <path d="M36 46 L44 54 L62 36" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </>
      )
    case 'gate':
      return (
        <>
          <ellipse cx="48" cy="78" rx="26" ry="7" fill="rgba(0,0,0,0.35)" />
          <path d="M18 22 H78 Q84 22 84 28 V56 Q84 62 78 62 H46 L30 76 V62 H18 Q12 62 12 56 V28 Q12 22 18 22Z" fill={fill} stroke={stroke} strokeWidth="1.5" filter={`url(#ag-glow-${id})`} />
          <path d="M40 34 V52 M56 34 V52" stroke="#042f2e" strokeWidth="4" strokeLinecap="round" />
        </>
      )
    case 'archivist':
      return (
        <>
          <ellipse cx="48" cy="78" rx="26" ry="7" fill="rgba(0,0,0,0.35)" />
          <path d="M14 32 V76 Q14 82 20 82 H76 Q82 82 82 76 V40 Q82 34 76 34 H50 L42 26 H20 Q14 26 14 32Z" fill={fill} stroke={stroke} strokeWidth="1.5" filter={`url(#ag-glow-${id})`} />
          <path d="M32 48 H64 M32 58 H64 M32 68 H48" stroke="rgba(255,255,255,0.45)" strokeWidth="1.5" />
          <circle cx="66" cy="66" r="5" fill="none" stroke="#fff" strokeWidth="1.2" opacity="0.7" />
          <path d="M64 66 L68 70 L74 62" stroke="#fff" strokeWidth="1.2" fill="none" />
        </>
      )
    default:
      return <circle cx="48" cy="44" r="24" fill={fill} stroke={stroke} />
  }
}

export default function AgentIcon3D({ id, tone }: { id: string; tone: Tone }) {
  return (
    <div className={`ag-icon-stage tone-${tone}`} aria-hidden="true">
      <div className="ag-icon-halo" />
      <div className="ag-icon-pedestal" />
      <div className="ag-icon-tilt">
        <div className="ag-icon-plate">
          <svg viewBox="0 0 96 96" className="ag-icon-svg" role="img">
            <Defs id={id} />
            <Art id={id} />
          </svg>
        </div>
      </div>
    </div>
  )
}
