import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, apiUrl } from '../api/client'
import RadarVerdict from '../components/RadarVerdict'
import { CATEGORY_LABELS } from '../api/types'
import type { PositionCheck, RiskEventFeedItem } from '../api/types'

// Everything /vessels/{mmsi}/info knows about a ship (registry + watchlist).
interface VesselInfo {
  mmsi: number
  name: string | null
  imo: string | null
  callsign: string | null
  ship_type: string | null
  destination: string | null
  flag: string | null
  length_m: number | null
  beam_m: number | null
  first_seen: string | null
  last_seen: string | null
  on_watchlist: boolean
  category: string | null
  risk_score: number | null
  notes: string | null
  patterns: string[]
  last_pos: {
    ts: string
    lat: number
    lon: number
    sog: number | null
    cog: number | null
    heading: number | null
    source: string
  } | null
}

function fmtUtc(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC' }) + ' UTC'
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <><dt>{label}</dt><dd>{children}</dd></>
}

// The concrete "what" of an event: name 'DRISHTI' → 'UNKNOWN', draught
// 11.2 m → 8.5 m, met SOME SHIP at 31.5, 32.3 - instead of only the rule name.
function eventDetail(e: RiskEventFeedItem): string {
  const d = (e.detail ?? {}) as Record<string, unknown>
  const bits: string[] = []
  if (typeof d.field === 'string' && (d.old != null || d.new != null)) {
    bits.push(`${d.field}: '${d.old ?? '?'}' → '${d.new ?? '?'}'`)
  }
  if (typeof d.program === 'string' && d.program) bits.push(d.program)
  if (typeof d.old_draught === 'number' && typeof d.new_draught === 'number') {
    bits.push(`${d.old_draught} m → ${d.new_draught} m`)
  }
  const other = d.other_name ?? d.other
  if (typeof other === 'string' && other) bits.push(`with ${other}`)
  else if (typeof other === 'number') bits.push(`with MMSI ${other}`)
  if (typeof d.implied_kn === 'number') bits.push(`implied ${d.implied_kn} kn`)
  if (typeof d.mismatch_nm === 'number') bits.push(`${d.mismatch_nm} nm off prediction`)
  if (typeof d.lat === 'number' && typeof d.lon === 'number') {
    bits.push(`at ${d.lat.toFixed(3)}, ${d.lon.toFixed(3)}`)
  }
  return bits.join(' · ')
}

function CheckVerdict({ c }: { c: PositionCheck }) {
  return <RadarVerdict hull={c.hull_detected} persistent={c.persistent_target}
                       offsetM={c.nearest_offset_m} targetLengthM={c.target_length_m}
                       sizeMatch={c.size_match} />
}

// Compact ship dossier: the panel's short info plus the full evidence trail
// (events, satellite verification) as a real page, no map needed.
export default function ShipDetails() {
  const { mmsi } = useParams()
  const [info, setInfo] = useState<VesselInfo | null>(null)
  const [checks, setChecks] = useState<PositionCheck[]>([])
  const [checksLocked, setChecksLocked] = useState(0)
  const [events, setEvents] = useState<RiskEventFeedItem[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!mmsi) return
    let cancelled = false
    setInfo(null); setChecks([]); setEvents([]); setError(null)
    api<VesselInfo>(`/vessels/${mmsi}/info`)
      .then((i) => { if (!cancelled) setInfo(i) })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)) })
    api<{ locked: number; items: PositionCheck[] }>(`/vessels/${mmsi}/position-checks`)
      .then((c) => { if (!cancelled) { setChecks(c.items); setChecksLocked(c.locked) } })
      .catch(() => {})
    api<RiskEventFeedItem[]>(`/vessels/${mmsi}/events`)
      .then((e) => { if (!cancelled) setEvents(e) }).catch(() => {})
    return () => { cancelled = true }
  }, [mmsi])

  if (error) return <div className="page"><p className="error">{error}</p></div>
  if (!info) return <div className="page"><p className="empty">Loading ship…</p></div>

  const lp = info.last_pos
  return (
    <div className="page dossier">
      <header className="dossier-head">
        <h1>{info.name ?? info.mmsi}</h1>
        {info.category
          ? <span className={`tag ${info.category}`}>{CATEGORY_LABELS[info.category] ?? info.category}</span>
          : <span className="tag closed">{info.on_watchlist ? 'on watchlist' : 'ordinary traffic'}</span>}
        {info.risk_score != null && info.risk_score > 0 && (
          <span className={`risk-badge${info.risk_score >= 200 ? ' high' : ''}`}
                title="summed risk-event score over the last 30 days">
            RISK {Math.round(info.risk_score)}
          </span>
        )}
        <span className="spacer" />
        <Link to={`/ship/${info.mmsi}`} className="map-btn">◉ View on map</Link>
      </header>

      <div className="dossier-grid">
        <section className="card">
          <h2>Identity</h2>
          <dl className="datagrid">
            <Row label="MMSI"><span className="mono">{info.mmsi}</span></Row>
            {info.imo && <Row label="IMO"><span className="mono">{info.imo}</span></Row>}
            {info.callsign && <Row label="Callsign"><span className="mono">{info.callsign}</span></Row>}
            {info.ship_type && <Row label="Type">{info.ship_type}</Row>}
            {info.flag && <Row label="Flag">{info.flag}</Row>}
            {info.length_m != null && (
              <Row label="Size">{Math.round(info.length_m)} m{info.beam_m != null ? ` × ${Math.round(info.beam_m)} m` : ''}</Row>
            )}
            {info.destination && <Row label="Destination">{info.destination}</Row>}
            {info.first_seen && <Row label="First seen">{fmtUtc(info.first_seen)}</Row>}
            {info.last_seen && <Row label="Last seen">{fmtUtc(info.last_seen)}</Row>}
          </dl>
        </section>

        <section className="card">
          <h2>Last position</h2>
          {lp ? (
            <dl className="datagrid">
              <Row label="Position"><span className="mono">{lp.lat.toFixed(4)}, {lp.lon.toFixed(4)}</span></Row>
              {lp.sog != null && <Row label="Speed">{lp.sog.toFixed(1)} kn</Row>}
              {lp.cog != null && <Row label="Course">{lp.cog.toFixed(0)}°</Row>}
              <Row label="Received">{fmtUtc(lp.ts)}</Row>
              <Row label="Source">{lp.source}</Row>
            </dl>
          ) : <p className="empty">No position received yet.</p>}
        </section>
      </div>

      {(info.patterns?.length > 0 || info.notes) && (
        <section className="card card-row">
          <h2>Why it&apos;s watched</h2>
          {info.patterns?.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem', margin: '0.5rem 0' }}>
              {info.patterns.map((p) => (
                <span key={p} className={`tag ${p.includes('list') || p.includes('ban') || p.includes('detention') ? 'shadow_fleet' : 'open'}`}>{p}</span>
              ))}
            </div>
          )}
          {info.notes && <p style={{ fontSize: 13.5, lineHeight: 1.6, maxWidth: 760 }}>{info.notes}</p>}
        </section>
      )}

      {checks.length > 0 && (
        <section className="card card-row">
          <h2>Satellite verification</h2>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr><th>Chip</th><th>Source</th><th>Captured</th><th>Δ claim</th><th>Radar verdict</th><th>Verify</th></tr>
              </thead>
              <tbody>
                {checks.map((c) => (
                  <tr key={c.id}>
                    <td>
                      {c.chip_key ? (
                        <a href={apiUrl(`/position-checks/${c.id}/chip`)} target="_blank" rel="noreferrer">
                          <img src={apiUrl(`/position-checks/${c.id}/chip`)} alt="SAR chip"
                               className="chip-thumb" loading="lazy" />
                        </a>
                      ) : <span className="mono" style={{ color: 'var(--muted)', fontSize: 11 }}>-</span>}
                    </td>
                    <td><span className={`tag ${c.source === 'sentinel-1' ? 'other' : 'shadow_fleet'}`}>{c.source}</span></td>
                    <td className="mono" style={{ fontSize: 12 }}>{fmtUtc(c.acquired_at)}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{c.delta_minutes.toFixed(0)} min</td>
                    <td style={{ fontSize: 12 }}><CheckVerdict c={c} /></td>
                    <td>
                      {c.browser_url && (
                        <a href={c.browser_url} target="_blank" rel="noreferrer"
                           style={{ color: 'var(--watch-other)', fontSize: 12, fontWeight: 600 }}>
                          verify hull →
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {checksLocked > 0 && (
            <p style={{ marginTop: '0.6rem' }}>
              <Link to="/login" style={{ color: 'var(--watch-other)', fontWeight: 600 }}>
                Log in to see {checksLocked} more satellite capture{checksLocked > 1 ? 's' : ''} →
              </Link>
            </p>
          )}
        </section>
      )}

      {events.length > 0 && (
        <section className="card card-row">
          <h2>Event timeline</h2>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr><th>Event</th><th>What changed</th><th>Severity</th><th>Score</th><th>When</th></tr>
              </thead>
              <tbody>
                {events.map((e, i) => (
                  <tr key={`${e.rule}-${e.ts}-${i}`} className={`evt-row ${e.severity}`}>
                    <td><span className={`evt-dot ${e.severity}`} /> {e.label}</td>
                    <td className="mono" style={{ fontSize: 12, color: 'var(--text)' }}>{eventDetail(e) || '-'}</td>
                    <td><span className={`tag evt-sev ${e.severity}`}>{e.severity}</span></td>
                    <td className="mono">{Math.round(e.score)}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{fmtUtc(e.ts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {info.on_watchlist && checks.length === 0 && (
        <p className="empty" style={{ marginTop: '1.4rem' }}>
          No satellite captures matched yet - they appear automatically as
          Sentinel passes cover the ship&apos;s reported positions.
        </p>
      )}

    </div>
  )
}
