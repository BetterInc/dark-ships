// The automated SAR verdict for one position check, worded identically
// everywhere it appears (map panel, imagery table, ship dossier).
export default function RadarVerdict({
  hull,
  persistent,
  offsetM,
  targetLengthM,
  sizeMatch,
}: {
  hull: boolean | null
  persistent: boolean | null
  offsetM: number | null
  targetLengthM: number | null
  sizeMatch: boolean | null
}) {
  if (hull == null) {
    return <span className="mono" style={{ color: 'var(--muted)' }}>pending</span>
  }
  if (hull) {
    const len = targetLengthM != null ? ` · ~${Math.round(targetLengthM)} m` : ''
    const off = offsetM != null ? ` · ${Math.round(offsetM)} m off` : ''
    return persistent ? (
      <span style={{ color: '#f2b134' }}
            title="the same spot was bright on a pass weeks earlier - fixed structure or a long-anchored ship">
        ■ persistent target at claim{len}{off}
      </span>
    ) : (
      <span style={{ color: '#34d399' }}>■ ship at claimed spot{len}{off}</span>
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
