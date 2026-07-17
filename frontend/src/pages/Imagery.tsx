import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiUrl, usePolling } from '../api/client'
import Pagination from '../components/Pagination'
import RadarVerdict from '../components/RadarVerdict'

interface ImageryItem {
  kind: 'position_check' | 'gap_scene'
  mmsi: number
  vessel_name: string | null
  source: string
  acquired_at: string
  lat: number
  lon: number
  delta_minutes: number | null
  gap_id: number | null
  product_name: string | null
  quicklook_url: string | null
  browser_url: string | null
  check_id: number | null
  optical_chip_key: string | null
  optical_hull_detected: boolean | null
  sts_pair_detected: boolean | null
  hull_detected: boolean | null
  target_count: number | null
  nearest_offset_m: number | null
  persistent_target: boolean | null
  target_length_m: number | null
  size_match: boolean | null
  chip_key: string | null
}

interface ImageryPage {
  total: number
  items: ImageryItem[]
}

const PAGE_SIZE = 25

function fmtUtc(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC' }) + ' UTC'
}

// Deep-link into the Copernicus Browser zoomed tight (z=16) on the exact
// coordinate, so the ship fills the view instead of the 110 km wide scene.
function zoomedBrowserUrl(lat: number, lon: number, acquiredAt: string): string {
  const day = acquiredAt.slice(0, 10)
  return `https://browser.dataspace.copernicus.eu/?zoom=16&lat=${lat.toFixed(4)}&lng=${lon.toFixed(4)}` +
    `&fromTime=${day}T00%3A00%3A00.000Z&toTime=${day}T23%3A59%3A59.999Z`
}

// Chip PNG (our own measured SAR crop) when stored, else the scene quicklook.
function Thumb({ item }: { item: ImageryItem }) {
  const [broken, setBroken] = useState(false)
  const [hover, setHover] = useState(false)
  const chip = item.check_id != null && item.chip_key
    ? apiUrl(`/position-checks/${item.check_id}/chip`) : null
  const optical = item.check_id != null && item.optical_chip_key
    ? apiUrl(`/position-checks/${item.check_id}/chip?kind=optical`) : null
  const base = chip ?? item.quicklook_url
  // on hover, swap the radar chip to the true-colour Sentinel-2 image if one exists
  const src = hover && optical ? optical : base
  useEffect(() => { setBroken(false) }, [src])
  if (!base || broken) {
    return (
      <div style={{
        width: 56, height: 56, borderRadius: 4, border: '1px solid var(--line)',
        display: 'grid', placeItems: 'center', color: 'var(--muted)', fontSize: 9,
      }} className="mono">no img</div>
    )
  }
  const showingOptical = hover && optical
  const img = (
    <img src={src ?? base} alt={chip ? 'SAR chip around claimed position' : (item.product_name ?? 'satellite scene')}
      title={optical ? 'radar chip - hover for the true-colour daylight image'
             : chip ? '3x3 km SAR chip around the claimed position - click to enlarge' : 'scene quicklook'}
      style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 4,
               border: showingOptical ? '1px solid #f2b134'
                       : chip ? '1px solid var(--watch-other)' : '1px solid var(--line)' }}
      onError={() => setBroken(true)} loading="lazy" />
  )
  return (
    <a href={optical ?? src ?? base} target="_blank" rel="noreferrer"
       onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
       style={{ position: 'relative', display: 'inline-block' }}>
      {img}
      {optical && (
        <span style={{ position: 'absolute', bottom: 1, right: 1, fontSize: 9,
                       background: 'rgba(0,0,0,0.6)', color: '#f2b134',
                       borderRadius: 2, padding: '0 2px', lineHeight: 1.4 }}
              title="a true-colour daylight image is available - hover">◐</span>
      )}
    </a>
  )
}

function Verdict({ item }: { item: ImageryItem }) {
  if (item.kind !== 'position_check') return <span className="mono" style={{ color: 'var(--muted)' }}>-</span>
  return <RadarVerdict hull={item.hull_detected} persistent={item.persistent_target}
                       offsetM={item.nearest_offset_m} targetLengthM={item.target_length_m}
                       sizeMatch={item.size_match} opticalHull={item.optical_hull_detected}
                       stsPair={item.sts_pair_detected} />
}

export default function Imagery() {
  const [page, setPage] = useState(0)
  // default to the auto-verified radar checks; the thousands of raw AIS-gap
  // scenes stay one filter click away instead of burying the evidence
  const [kind, setKind] = useState('position_check')
  const [source, setSource] = useState('all')
  const [verdict, setVerdict] = useState('all')

  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
  if (kind !== 'all') params.set('kind', kind)
  if (source !== 'all') params.set('source', source)
  if (verdict !== 'all') params.set('verdict', verdict)
  const { data, error } = usePolling<ImageryPage>(`/imagery?${params}`, 60_000)

  useEffect(() => { setPage(0) }, [kind, source, verdict])
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  useEffect(() => { if (page > pageCount - 1) setPage(pageCount - 1) }, [page, pageCount])

  return (
    <div className="page">
      <h1>Imagery</h1>
      <p className="sub">
        Every satellite capture matched against a watchlist ship&apos;s position.
        Sentinel-1 (radar) checks are verified automatically: an AI ship
        detector finds hulls in the 3&times;3 km chip around the claimed spot,
        checks whether the target&apos;s measured size can be that ship (per its
        AIS dimensions), and compares against a pass weeks earlier to rule out
        fixed structures. The chip is stored as evidence.{' '}
        <strong>Zoom to ship</strong> opens the Copernicus Browser on the
        coordinate for human verification.
      </p>

      {error && <p className="error">{error}</p>}

      <div className="filters">
        <label className="field">
          Kind
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="all">All kinds</option>
            <option value="position_check">Claimed-position checks</option>
            <option value="gap_scene">AIS-gap scenes</option>
          </select>
        </label>
        <label className="field">
          Source
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="all">All satellites</option>
            <option value="sentinel-1">Sentinel-1 (radar)</option>
            <option value="sentinel-2">Sentinel-2 (optical)</option>
          </select>
        </label>
        <label className="field">
          Verdict
          <select value={verdict} onChange={(e) => setVerdict(e.target.value)}>
            <option value="all">All verdicts</option>
            <option value="hull">Target at claim</option>
            <option value="no_target">No target</option>
            <option value="pending">Not analyzed</option>
          </select>
        </label>
        <span className="filter-count">{total} captures</span>
      </div>

      {total > 0 && (
        <Pagination page={page} pageCount={pageCount} total={total} pageSize={PAGE_SIZE} onPage={setPage} />
      )}

      {data && data.items.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Preview</th>
                <th>Ship</th>
                <th>Source</th>
                <th>Kind</th>
                <th>Captured</th>
                <th>Δ claim</th>
                <th>Radar verdict</th>
                <th>Verify</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item, i) => (
                <tr key={`${item.kind}-${item.check_id ?? item.gap_id}-${i}`}>
                  <td><Thumb item={item} /></td>
                  <td>
                    <Link to={`/ship/${item.mmsi}`}>{item.vessel_name ?? item.mmsi}</Link>
                    <div className="mono" style={{ fontSize: 11, color: 'var(--muted)' }}>{item.mmsi}</div>
                  </td>
                  <td>
                    <span className={`tag ${item.source === 'sentinel-1' ? 'other' : 'shadow_fleet'}`}>{item.source}</span>
                  </td>
                  <td className="mono" style={{ fontSize: 12 }}>
                    {item.kind === 'position_check' ? 'position check' : `gap scene #${item.gap_id}`}
                  </td>
                  <td className="mono" style={{ fontSize: 12 }}>{fmtUtc(item.acquired_at)}</td>
                  <td className="mono" style={{ fontSize: 12 }}>
                    {item.delta_minutes != null ? `${item.delta_minutes.toFixed(0)} min` : '-'}
                  </td>
                  <td style={{ fontSize: 12 }}><Verdict item={item} /></td>
                  <td>
                    <a href={zoomedBrowserUrl(item.lat, item.lon, item.acquired_at)} target="_blank"
                       rel="noreferrer" style={{ color: 'var(--watch-other)', fontWeight: 600, fontSize: 12 }}>
                      Zoom to ship →
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > 0 && (
        <Pagination page={page} pageCount={pageCount} total={total} pageSize={PAGE_SIZE} onPage={setPage} />
      )}

      {data && total === 0 && (
        <p className="empty">
          No satellite captures match. Matches appear automatically when a
          Sentinel satellite passes over a tracked ship: Sentinel-1 radar crosses
          any spot around dawn and dusk (~05:30 / ~17:30 local) every 1-3 days,
          Sentinel-2 optical mid-morning every 2-5 days. The matcher re-runs
          every 6 hours.
        </p>
      )}
    </div>
  )
}
