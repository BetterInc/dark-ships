// The automated SAR verdict for one position check, worded identically
// everywhere it appears (map panel, imagery table, ship dossier).
export default function RadarVerdict({
  hull,
  persistent,
  offsetM,
  targetLengthM,
  sizeMatch,
  opticalHull = null,
}: {
  hull: boolean | null
  persistent: boolean | null
  offsetM: number | null
  targetLengthM: number | null
  sizeMatch: boolean | null
  opticalHull?: boolean | null
}) {
  if (hull == null) {
    return (
      <span className="mono" style={{ color: 'var(--muted)' }}
            title="scene matched; automatic radar analysis runs within a few hours">
        queued
      </span>
    )
  }
  if (hull) {
    const len = targetLengthM != null ? ` · ~${Math.round(targetLengthM)} m` : ''
    const off = offsetM != null ? ` · ${Math.round(offsetM)} m off` : ''
    const conf = opticalHull ? ' · daylight ✓' : ''  // radar + optical both confirm
    return persistent ? (
      <span style={{ color: '#f2b134' }}
            title="the same spot was bright on a pass weeks earlier - fixed structure or a long-anchored ship">
        ■ persistent target at claim{len}{off}
      </span>
    ) : (
      <span style={{ color: '#34d399' }}
            title={opticalHull ? 'confirmed by radar and a daylight optical image' : undefined}>
        ■ ship at claimed spot{len}{off}{conf}
      </span>
    )
  }
  // radar found nothing - but the optical model DID see a vessel in daylight.
  // Radar misses wooden/small hulls and ships in rough seas; this catches them.
  if (opticalHull) {
    return (
      <span style={{ color: '#38bdf8' }}
            title="radar found no target, but the optical model detected a vessel in a cloud-free daylight image - radar can miss wooden/small hulls">
        ◐ not on radar, but visible in daylight
      </span>
    )
  }
  if (sizeMatch === false) {
    return (
      <span style={{ color: '#f2b134' }}
            title="a radar target sits near the claim, but its measured size cannot be this ship">
        △ target near claim, wrong size{targetLengthM != null ? ` (~${Math.round(targetLengthM)} m)` : ''}
      </span>
    )
  }
  return <span style={{ color: '#ef4444' }}>□ no ship at claimed spot</span>
}
