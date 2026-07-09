import { useState } from 'react'
import { usePolling } from '../api/client'

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
}

function fmtUtc(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC' }) + ' UTC'
}

function Thumb({ item }: { item: ImageryItem }) {
  const [broken, setBroken] = useState(false)
  if (!item.quicklook_url || broken) {
    return (
      <div style={{
        width: 110, height: 110, borderRadius: 4, border: '1px solid var(--line)',
        display: 'grid', placeItems: 'center', color: 'var(--muted)', fontSize: 11,
      }} className="mono">no preview</div>
    )
  }
  return (
    <img src={item.quicklook_url} alt={item.product_name ?? 'satellite scene'}
      style={{ width: 110, height: 110, objectFit: 'cover', borderRadius: 4, border: '1px solid var(--line)' }}
      onError={() => setBroken(true)} />
  )
}

// Deep-link into the Copernicus Browser zoomed tight (z=16) on the exact
// coordinate, so the ship fills the view instead of the 110 km wide scene.
function zoomedBrowserUrl(lat: number, lon: number, acquiredAt: string): string {
  const day = acquiredAt.slice(0, 10)
  return `https://browser.dataspace.copernicus.eu/?zoom=16&lat=${lat.toFixed(4)}&lng=${lon.toFixed(4)}` +
    `&fromTime=${day}T00%3A00%3A00.000Z&toTime=${day}T23%3A59%3A59.999Z`
}

export default function Imagery() {
  const { data: items, error } = usePolling<ImageryItem[]>('/imagery', 60_000)

  return (
    <div className="page">
      <div className="under-construction">
        <span className="uc-badge">Under construction</span>
        This view is being integrated - the data below is preliminary and may shift as the feature lands.
      </div>
      <h1>Imagery</h1>
      <p className="sub">
        Every satellite capture the system has matched against a watchlist ship&apos;s
        position. The thumbnail is the <strong>full 110 km scene</strong> - a ship is a
        speck at that scale, so use <strong>&quot;Zoom to ship&quot;</strong> to open the
        Copernicus Browser tight on the exact coordinate and read the hull. Radar
        (Sentinel-1) sees through clouds and dark; optical (Sentinel-2) needs daylight.
      </p>

      {error && <p className="error">{error}</p>}

      {items?.map((item, i) => (
        <article key={i} className="gap-card">
          <div className="imagery-row">
            <Thumb item={item} />
            <div className="imagery-meta">
              <header style={{ display: 'flex', gap: '0.7rem', alignItems: 'baseline', flexWrap: 'wrap' }}>
                <h3>{item.vessel_name ?? `MMSI ${item.mmsi}`}</h3>
                <span className={`tag ${item.source === 'sentinel-1' ? 'other' : 'shadow_fleet'}`}>{item.source}</span>
                <span className={`tag ${item.kind === 'position_check' ? 'closed' : 'open'}`}>
                  {item.kind === 'position_check' ? 'claimed-position check' : 'AIS-gap scene'}
                </span>
              </header>
              <div className="gap-meta">
                <div>Captured<b>{fmtUtc(item.acquired_at)}</b></div>
                <div>Position<b>{item.lat.toFixed(3)}, {item.lon.toFixed(3)}</b></div>
                {item.delta_minutes != null && <div>Claim vs capture<b>Δ {item.delta_minutes.toFixed(0)} min</b></div>}
                {item.gap_id != null && <div>Gap<b>#{item.gap_id}</b></div>}
              </div>
              <div className="mono" style={{ color: 'var(--muted)', fontSize: 12, margin: '0.3rem 0' }}>
                {item.product_name}
              </div>
              <a href={zoomedBrowserUrl(item.lat, item.lon, item.acquired_at)} target="_blank"
                 rel="noreferrer" style={{ color: 'var(--watch-other)', fontWeight: 600 }}>
                Zoom to ship →
              </a>
              {item.browser_url && (
                <a href={item.browser_url} target="_blank" rel="noreferrer"
                   style={{ color: 'var(--muted)', marginLeft: '1rem', fontSize: 12 }}>
                  wide scene
                </a>
              )}
            </div>
          </div>
        </article>
      ))}

      {items && items.length === 0 && (
        <p className="empty">
          No satellite captures matched yet. Matches appear automatically when a
          Sentinel satellite passes over a tracked ship: Sentinel-1 radar crosses any
          spot around dawn and dusk (~05:30 / ~17:30 local) every 1-3 days,
          Sentinel-2 optical mid-morning every 2-5 days. Tracking started today -
          expect the first radar captures after the next dusk pass. The matcher
          re-runs every 6 hours.
        </p>
      )}
    </div>
  )
}
