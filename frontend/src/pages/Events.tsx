import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { usePolling } from '../api/client'
import Pagination from '../components/Pagination'
import type { RiskEventFeedItem } from '../api/types'

const PAGE_SIZE = 25

function fmtUtc(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC' }) + ' UTC'
}

function relTime(iso: string): string {
  const secs = (Date.now() - new Date(iso).getTime()) / 1000
  if (secs < 60) return 'just now'
  const mins = secs / 60
  if (mins < 60) return `${Math.floor(mins)}m ago`
  const hours = mins / 60
  if (hours < 24) return `${Math.floor(hours)}h ago`
  const days = hours / 24
  if (days < 30) return `${Math.floor(days)}d ago`
  return `${Math.floor(days / 30)}mo ago`
}

const SEVERITY_LABEL: Record<string, string> = {
  high: 'high',
  medium: 'medium',
  low: 'low',
}

// map a rule to a coarse type bucket for the type filter
const TYPE_BUCKETS: Record<string, string[]> = {
  dark: ['regional_gap', 'gfw_ais_gap'],
  sts: ['rendezvous', 'gfw_encounter', 'draught_change', 'oil_slick', 'dark_association'],
  spoof: ['impossible_jump', 'circle_spoofing', 'mmsi_collision', 'identity_change',
    'identity_integrity', 'flag_hop'],
}

function typeBucket(rule: string): string {
  if (rule.startsWith('risklist_')) return 'sanctions'
  for (const [bucket, rules] of Object.entries(TYPE_BUCKETS)) {
    if (rules.includes(rule)) return bucket
  }
  return 'other'
}

const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All types' },
  { value: 'sanctions', label: 'Sanctions and lists' },
  { value: 'dark', label: 'Went dark' },
  { value: 'sts', label: 'Ship-to-ship' },
  { value: 'spoof', label: 'Spoofing and identity' },
  { value: 'other', label: 'Other' },
]

/** Pull the most useful bits out of the free-form detail blob for display. */
function detailBits(detail: Record<string, unknown>): string[] {
  const bits: string[] = []
  const other = detail.other_name ?? detail.other
  if (typeof other === 'string' && other) bits.push(`with ${other}`)
  else if (typeof other === 'number') bits.push(`with MMSI ${other}`)
  const lat = detail.lat
  const lon = detail.lon
  if (typeof lat === 'number' && typeof lon === 'number') {
    bits.push(`${lat.toFixed(3)}, ${lon.toFixed(3)}`)
  }
  if (typeof detail.new_value === 'string' && detail.new_value) {
    bits.push(`now "${detail.new_value}"`)
  }
  return bits
}

export default function Events() {
  const { data: events, error } = usePolling<RiskEventFeedItem[]>('/me/events', 30_000)
  const [severity, setSeverity] = useState('all')
  const [type, setType] = useState('all')
  const [page, setPage] = useState(0)

  const filtered = useMemo(() => {
    if (!events) return []
    return events.filter((e) =>
      (severity === 'all' || e.severity === severity) &&
      (type === 'all' || typeBucket(e.rule) === type))
  }, [events, severity, type])

  const hasEvents = (events?.length ?? 0) > 0
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  useEffect(() => { setPage(0) }, [severity, type])
  useEffect(() => { if (page > pageCount - 1) setPage(pageCount - 1) }, [page, pageCount])
  const pageItems = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)

  return (
    <div className="page">
      <h1>Events</h1>
      <p className="sub">
        A live feed of every risk event for the ships on your watchlist - going
        dark, sanctions hits, ship-to-ship transfers, spoofing, draught changes
        and more. Newest first; refreshes automatically.
      </p>

      {error && <p className="error">{error}</p>}

      {hasEvents && (
        <div className="filters">
          <label className="field">
            Severity
            <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="all">All severities</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>
          <label className="field">
            Type
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <span className="filter-count">
            {filtered.length} of {events?.length ?? 0} events
          </span>
        </div>
      )}

      {hasEvents && filtered.length > 0 && (
        <Pagination page={page} pageCount={pageCount} total={filtered.length} pageSize={PAGE_SIZE} onPage={setPage} />
      )}

      {hasEvents && filtered.length > 0 && (
        <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Ship</th>
              <th>MMSI</th>
              <th>Event</th>
              <th>Severity</th>
              <th>Details</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((evt, i) => {
              const bits = detailBits(evt.detail)
              return (
                <tr key={`${evt.mmsi}-${evt.ts}-${evt.rule}-${i}`} className={`evt-row ${evt.severity}`}>
                  <td>{evt.vessel_name}</td>
                  <td className="mono">{evt.mmsi}</td>
                  <td>
                    <span className={`evt-dot ${evt.severity}`} /> {evt.label}
                  </td>
                  <td>
                    <span className={`tag evt-sev ${evt.severity}`}>{SEVERITY_LABEL[evt.severity]}</span>
                  </td>
                  <td className="mono evt-details">{bits.length > 0 ? bits.join(' · ') : '-'}</td>
                  <td className="mywatch-seen" title={fmtUtc(evt.ts)}>{relTime(evt.ts)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
</div>
      )}

      {hasEvents && filtered.length > 0 && (
        <Pagination page={page} pageCount={pageCount} total={filtered.length} pageSize={PAGE_SIZE} onPage={setPage} />
      )}

      {hasEvents && filtered.length === 0 && (
        <p className="empty">No events match these filters.</p>
      )}

      {events && events.length === 0 && (
        <p className="empty">
          No risk events yet. Add ships to your watchlist (<Link to="/">Map</Link>{' '}
          or Suggestions) and their risk events - going dark, sanctions hits,
          ship-to-ship transfers, spoofing - will appear here live.{' '}
          <Link to="/watchlist">Go to your watchlist</Link>.
        </p>
      )}
    </div>
  )
}
