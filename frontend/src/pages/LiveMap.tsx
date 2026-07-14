import maplibregl, { GeoJSONSource, Map as MLMap } from 'maplibre-gl'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, usePolling } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { CATEGORY_LABELS } from '../api/types'
import type { Cluster, LatestPosition, PositionCheck, Region, TrackPoint, WorldPosition } from '../api/types'

// Compact "+ Watchlist" control for the vessel info panels. Adds a ship to the
// logged-in user's private watchlist; logged-out users are sent to login.
function FollowButton({
  mmsi,
  followed,
  onAdded,
}: {
  mmsi: number
  followed: Set<number>
  onAdded: (mmsi: number) => void
}) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  if (followed.has(mmsi)) {
    return (
      <button className="ghost" disabled style={{ marginTop: '0.8rem', opacity: 0.7 }}>
        On your watchlist
      </button>
    )
  }

  async function click() {
    if (!user) {
      navigate('/login')
      return
    }
    setBusy(true)
    setNote(null)
    try {
      await api('/me/watchlist', { method: 'POST', body: JSON.stringify({ mmsi }) })
      onAdded(mmsi)
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ marginTop: '0.8rem', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.5rem' }}>
      <button className="ghost" disabled={busy} onClick={click}>
        {user ? '+ Watchlist' : 'Log in to follow'}
      </button>
      {note && <span className="error" style={{ margin: 0 }}>{note}</span>}
    </div>
  )
}

// Copy (or native-share) a public deep link to this vessel. The link opens the
// map focused on the ship, viewable by anyone - no login required.
function ShareButton({ mmsi }: { mmsi: number }) {
  const [copied, setCopied] = useState(false)
  async function share() {
    const url = `${window.location.origin}/ship/${mmsi}`
    try {
      if (navigator.share) {
        await navigator.share({ title: `Vessel ${mmsi} · Dark Ships`, url })
      } else {
        await navigator.clipboard.writeText(url)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }
    } catch {
      /* user dismissed the share sheet, or clipboard was blocked - no-op */
    }
  }
  return (
    <button className="ghost share-btn" onClick={share} style={{ marginTop: '0.5rem' }}>
      {copied ? 'Link copied' : 'Share ship'}
    </button>
  )
}

// Shared vessel-panel sections, so the live-feed panel and the ambient /
// deep-link panel render watchlist evidence identically and can't drift.
function PatternTags({ patterns }: { patterns: string[] }) {
  if (patterns.length === 0) return null
  return (
    <div className="panel-detail" style={{ marginTop: '0.7rem' }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--muted)', marginBottom: '0.35rem' }}>
        Detected pattern{patterns.length > 1 ? 's' : ''}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
        {patterns.map((p) => (
          <span key={p} className={`tag ${p.includes('list') || p.includes('ban') || p.includes('detention') ? 'shadow_fleet' : 'open'}`}>{p}</span>
        ))}
      </div>
    </div>
  )
}

function WatchNotes({ notes }: { notes: string | null | undefined }) {
  if (!notes) return null
  return (
    <div className="panel-detail" style={{ marginTop: '0.7rem' }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--muted)', marginBottom: '0.3rem' }}>
        Why it&apos;s watched
      </div>
      <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text)' }}>{notes}</div>
    </div>
  )
}

function SatChecks({ checks }: { checks: PositionCheck[] }) {
  if (checks.length === 0) return null
  return (
    <div className="panel-detail" style={{ marginTop: '0.9rem', borderTop: '1px dashed var(--line)', paddingTop: '0.6rem' }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--muted)', marginBottom: '0.4rem' }}>
        Satellite cross-checks - claimed position captured
      </div>
      {checks.slice(0, 5).map((c) => (
        <div key={c.id} style={{ fontSize: 12, marginBottom: '0.45rem' }} className="mono">
          {c.source} · {new Date(c.acquired_at).toLocaleString('en-GB', { timeZone: 'UTC' })} UTC
          · Δ{c.delta_minutes.toFixed(0)} min{' '}
          {c.browser_url && (
            <a href={c.browser_url} target="_blank" rel="noreferrer" style={{ color: 'var(--watch-other)' }}>
              verify hull →
            </a>
          )}
        </div>
      ))}
    </div>
  )
}

// Set of mmsis already on the logged-in user's watchlist, fetched once so
// followed ships show a done state instead of an add button.
function useFollowed(): { followed: Set<number>; addFollowed: (mmsi: number) => void } {
  const { user } = useAuth()
  const [followed, setFollowed] = useState<Set<number>>(new Set())
  useEffect(() => {
    if (!user) {
      setFollowed(new Set())
      return
    }
    let cancelled = false
    api<{ mmsi: number }[]>('/me/watchlist')
      .then((list) => { if (!cancelled) setFollowed(new Set(list.map((w) => w.mmsi))) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [user])
  const addFollowed = (mmsi: number) => setFollowed((prev) => new Set(prev).add(mmsi))
  return { followed, addFollowed }
}

// Maximally distinct hues so categories never blur into each other on the
// dark basemap: gold, purple, pink, red, cyan, green, slate.
const COLORS: Record<string, string> = {
  shadow_fleet: '#f2b134', // gold  - sanctioned oil tanker
  smuggling: '#a97bf0',    // purple - sanctioned cargo / contraband
  sabotage: '#f45ba8',     // pink  - cables / energy infrastructure
  narco: '#ef4444',        // red   - drug trafficking
  iuu_fishing: '#38bdf8',  // cyan  - illegal fishing
  other: '#34d399',        // green - behaviour flags only
  region: '#55708c',       // slate - ordinary traffic
}
const MAP_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    carto: {
      type: 'raster',
      tiles: ['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors © CARTO',
    },
  },
  layers: [{ id: 'carto', type: 'raster', source: 'carto' }],
}

const MOVING_SOG = 1.0  // above this a ship is underway -> arrow; else -> dot

interface SearchHit {
  mmsi: number
  name: string | null
  imo: string | null
  ship_type: string | null
  on_watchlist: boolean
  category: string | null
  lat: number | null
  lon: number | null
  live: boolean
}

interface AmbientInfo {
  mmsi: number
  name: string | null
  imo: string | null
  ship_type: string | null
  destination: string | null
  flag: string | null
  on_watchlist: boolean
  category: string | null
  risk_score: number | null
  notes: string | null
  patterns: string[]
  // last position actually received for this ship, however old (never guessed)
  last_pos: {
    ts: string
    lat: number
    lon: number
    sog: number | null
    cog: number | null
    heading: number | null
    source: string
  } | null
  // live values when the panel was opened from a map click; null when unknown
  lat: number | null
  lon: number | null
  sog: number | null
  bearing: number | null
}

// Build a small triangle icon (pointing "up" = north) for each category, so
// moving ships can render as rotatable arrows. Canvas-generated = no glyph
// server needed (this offline style has none, which is why text symbols fail).
// A sleek vessel-heading marker: a narrow arrow with a gently curved stern,
// drawn at 2x and registered with pixelRatio 2 so it stays crisp when rotated.
const ICON_PX = 2
function triangleIcon(color: string): ImageData {
  const s = 30
  const cv = document.createElement('canvas')
  cv.width = s * ICON_PX; cv.height = s * ICON_PX
  const ctx = cv.getContext('2d')!
  ctx.scale(ICON_PX, ICON_PX)
  ctx.beginPath()
  ctx.moveTo(s / 2, 3.5)                               // bow (north)
  ctx.lineTo(s * 0.7, s - 5)                           // right quarter
  ctx.quadraticCurveTo(s / 2, s * 0.74, s * 0.3, s - 5) // curved stern
  ctx.closePath()
  ctx.fillStyle = color
  ctx.strokeStyle = 'rgba(11,21,32,0.9)'
  ctx.lineWidth = 1.4
  ctx.lineJoin = 'round'
  ctx.fill()
  ctx.stroke()
  return ctx.getImageData(0, 0, s * ICON_PX, s * ICON_PX)
}

function vesselsToGeoJSON(positions: LatestPosition[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: positions.map((p) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: {
        mmsi: p.mmsi,
        name: p.vessel_name ?? p.ship_name ?? String(p.mmsi),
        kind: p.category ?? 'region',
        watchlist: p.category != null,
        risk: p.risk_score ?? 0,
        shipType: p.ship_type ?? '',
        sog: p.sog,
        moving: (p.sog ?? 0) > MOVING_SOG ? 1 : 0,
        // heading the ship points; fall back to course over ground
        bearing: p.heading ?? p.cog ?? 0,
        ts: p.ts,
      },
    })),
  }
}

function regionsToGeoJSON(regions: Region[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: regions.map((r) => {
      const [[latMin, lonMin], [latMax, lonMax]] = r.bbox
      return {
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[
            [lonMin, latMin], [lonMax, latMin], [lonMax, latMax], [lonMin, latMax], [lonMin, latMin],
          ]],
        },
        properties: { name: r.name },
      }
    }),
  }
}

export default function LiveMap() {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MLMap | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const [selected, setSelected] = useState<LatestPosition | null>(null)
  const [selectedCluster, setSelectedCluster] = useState<Cluster | null>(null)
  const [selectedAmbient, setSelectedAmbient] = useState<AmbientInfo | null>(null)
  const { mmsi: shipParam } = useParams()          // /ship/:mmsi (canonical)
  const [searchParams] = useSearchParams()         // ?ship= (legacy, still honoured)
  const navigate = useNavigate()
  const location = useLocation()
  const deepLinked = useRef(false)  // only auto-focus the shared link once
  // capture the incoming ship once, before the URL-sync effect can change it
  const initialShip = useRef(shipParam ?? searchParams.get('ship'))
  const [posChecks, setPosChecks] = useState<PositionCheck[]>([])
  // mobile: the legend / anchorage panels collapse behind toggle chips
  const [showLegend, setShowLegend] = useState(false)
  const [showClusters, setShowClusters] = useState(false)
  const { followed, addFollowed } = useFollowed()
  // default to the auto-watchlist threshold (50); remember the user's choice
  const [minRisk, setMinRisk] = useState(() => {
    const saved = localStorage.getItem('darkships.minRisk')
    return saved != null ? Number(saved) : 50
  })
  useEffect(() => { localStorage.setItem('darkships.minRisk', String(minRisk)) }, [minRisk])

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchHit[]>([])
  useEffect(() => {
    if (query.trim().length < 2) { setResults([]); return }
    const t = setTimeout(() => {
      api<SearchHit[]>(`/vessels/search?q=${encodeURIComponent(query.trim())}`)
        .then(setResults).catch(() => setResults([]))
    }, 250)
    return () => clearTimeout(t)
  }, [query])

  const openHit = (hit: SearchHit) => {
    setQuery(''); setResults([])
    const map = mapRef.current
    if (hit.lat != null && hit.lon != null && map) {
      map.flyTo({ center: [hit.lon, hit.lat], zoom: 11, essential: true })
    }
    // prefer the rich watchlist panel if we're already tracking this ship
    const pos = positions?.find((p) => p.mmsi === hit.mmsi)
    if (pos) {
      setSelectedCluster(null); setSelectedAmbient(null); setSelected(pos)
      return
    }
    api<Omit<AmbientInfo, 'lat' | 'lon' | 'sog' | 'bearing'>>(`/vessels/${hit.mmsi}/info`)
      .then((info) => { setSelected(null); setSelectedCluster(null)
        // only real received values - a missing position/speed stays unknown
        setSelectedAmbient({ ...info,
          lat: hit.lat ?? info.last_pos?.lat ?? null,
          lon: hit.lon ?? info.last_pos?.lon ?? null,
          sog: info.last_pos?.sog ?? null,
          bearing: info.last_pos?.cog ?? null }) })
      .catch(() => {})
  }

  // Focus a ship by MMSI - drives the shareable ?ship= deep link. Uses the
  // public search endpoint, so it resolves the ship's position even before the
  // position feeds have finished loading on first paint.
  const selectByMmsi = async (mmsi: number) => {
    const pos = positions?.find((p) => p.mmsi === mmsi)
    if (pos) {
      mapRef.current?.flyTo({ center: [pos.lon, pos.lat], zoom: 11, essential: true })
      setSelectedCluster(null); setSelectedAmbient(null); setSelected(pos)
      return
    }
    try {
      const hits = await api<SearchHit[]>(`/vessels/search?q=${mmsi}`)
      const hit = hits.find((h) => h.mmsi === mmsi) ?? hits[0]
      if (hit) openHit(hit)
    } catch {
      /* unknown / malformed mmsi - leave the map as-is */
    }
  }

  const { data: positions } = usePolling<LatestPosition[]>('/positions/latest', 30_000)
  const { data: regions } = usePolling<Region[]>('/regions', 300_000)
  const { data: world } = usePolling<WorldPosition[]>('/positions/world', 120_000)
  const { data: clusters } = usePolling<Cluster[]>('/clusters', 60_000)

  // Shareable deep link: on first load, focus the ship named in /ship/<mmsi>.
  // Reads the value captured at mount, so the URL-sync effect below can't race
  // it away before the map is ready. Waits for the first /positions/latest
  // poll so ships in the live feed open the rich watchlist panel (same as a
  // marker click) instead of falling back to the bare info panel.
  useEffect(() => {
    if (!mapReady || deepLinked.current) return
    const raw = initialShip.current
    if (!raw) return
    const mmsi = Number(raw)
    if (!Number.isFinite(mmsi) || mmsi <= 0) {
      deepLinked.current = true  // consume bad links so URL sync isn't blocked
      return
    }
    // the user picked a ship/cluster before the feed loaded - their choice wins
    if (selected || selectedAmbient || selectedCluster) {
      deepLinked.current = true
      return
    }
    if (positions) {
      deepLinked.current = true
      selectByMmsi(mmsi)
      return
    }
    // feed slow or erroring: still resolve the link via search/info after a
    // grace period, rather than holding it hostage to the 30s poll
    const t = window.setTimeout(() => {
      if (deepLinked.current) return
      deepLinked.current = true
      selectByMmsi(mmsi)
    }, 8000)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, positions, selected, selectedAmbient, selectedCluster])

  // Keep the address bar at /ship/<mmsi> for the open panel, so the URL itself
  // is the clean shareable link and previews can resolve the vessel.
  useEffect(() => {
    // don't touch the URL until a pending deep link has been consumed
    if (initialShip.current && !deepLinked.current) return
    const mmsi = selected?.mmsi ?? selectedAmbient?.mmsi
    const target = mmsi != null ? `/ship/${mmsi}` : '/'
    if (location.pathname !== target) navigate(target, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, selectedAmbient])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      center: [0, 35],
      zoom: 2.5,
    })
    mapRef.current = map

    map.on('load', () => {
      // register triangle icons (one per category + a neutral one for ambient
      // traffic) up front so any layer can draw moving ships as arrows
      for (const [cat, color] of Object.entries(COLORS)) {
        const id = `arrow-${cat}`
        if (!map.hasImage(id)) map.addImage(id, triangleIcon(color), { pixelRatio: ICON_PX })
      }

      // ambient world layer: every terrestrially received ship. Same language
      // as watchlist ships - a tiny arrow if it's underway (pointing where it's
      // going), a tiny dot if it's stopped/docked.
      map.addSource('world', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: 'world-dots',
        type: 'circle',
        source: 'world',
        filter: ['==', ['get', 'moving'], 0],
        paint: { 'circle-radius': 3, 'circle-color': '#5b7a99', 'circle-opacity': 0.85 },
      })
      map.addLayer({
        id: 'world-arrows',
        type: 'symbol',
        source: 'world',
        filter: ['==', ['get', 'moving'], 1],
        layout: {
          'icon-image': 'arrow-region',
          'icon-size': 0.5,
          'icon-rotate': ['get', 'bearing'],
          'icon-rotation-alignment': 'map',
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
        paint: { 'icon-opacity': 0.9 },
      })

      map.addSource('regions', { type: 'geojson', data: regionsToGeoJSON([]) })
      map.addLayer({
        id: 'regions-line',
        type: 'line',
        source: 'regions',
        paint: { 'line-color': '#46617c', 'line-width': 1, 'line-dasharray': [3, 3] },
      })

      // STS-staging clusters: a glowing halo under a huddle of sanctioned
      // ships. It's an OVERVIEW aid, so it fades out once you zoom in far
      // enough to see the individual vessels (else the centroid looks like a
      // phantom ship sitting on empty water).
      map.addSource('clusters', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: 'clusters-glow',
        type: 'circle',
        source: 'clusters',
        paint: {
          'circle-radius': ['+', 18, ['*', ['get', 'count'], 2]],
          'circle-color': '#e4604e',
          'circle-blur': 1,
          // soft blurred glow (an overview aid). A hollow RED RING means "ship
          // went dark" - the cluster is deliberately a soft glow, never a ring,
          // so the two can't be confused. Fades out when zoomed in past the
          // individual ships.
          'circle-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0.3, 9, 0.3, 11, 0],
        },
      })

      const categoryColor: maplibregl.ExpressionSpecification = [
        'match', ['get', 'kind'],
        'shadow_fleet', COLORS.shadow_fleet,
        'narco', COLORS.narco,
        'iuu_fishing', COLORS.iuu_fishing,
        'sabotage', COLORS.sabotage,
        'smuggling', COLORS.smuggling,
        'other', COLORS.other,
        COLORS.region,
      ]
      const cappedRisk: maplibregl.ExpressionSpecification = ['min', ['get', 'risk'], 150]

      map.addSource('vessels', { type: 'geojson', data: vesselsToGeoJSON([]) })
      // risk halo: a glow that grows with the score, so the worst ship pulls the eye first
      map.addLayer({
        id: 'vessels-halo',
        type: 'circle',
        source: 'vessels',
        filter: ['all', ['get', 'watchlist'], ['>', ['get', 'risk'], 0]],
        paint: {
          'circle-radius': ['+', 8, ['/', cappedRisk, 9]],
          'circle-color': categoryColor,
          'circle-blur': 1,
          'circle-opacity': 0.32,
        },
      })
      // Stationary ships (and ambient traffic) render as round dots. Moving
      // watchlist ships become arrows below, so exclude them here.
      // watchlist STATIONARY ships only. Ambient traffic is drawn by the world
      // layer (one consistent representation), moving watchlist ships by the
      // arrow layer below.
      map.addLayer({
        id: 'vessels-dots',
        type: 'circle',
        source: 'vessels',
        filter: ['all', ['get', 'watchlist'], ['==', ['get', 'moving'], 0]],
        paint: {
          'circle-radius': ['+', 5.5, ['/', cappedRisk, 28]],
          'circle-color': categoryColor,
          'circle-opacity': 1,
          'circle-stroke-width': 1.4,
          'circle-stroke-color': '#0b1520',
        },
      })
      // watchlist ships underway: arrows rotated to their heading (icons
      // registered at the top of load)
      map.addLayer({
        id: 'vessels-arrows',
        type: 'symbol',
        source: 'vessels',
        filter: ['all', ['get', 'watchlist'], ['==', ['get', 'moving'], 1]],
        layout: {
          'icon-image': [
            'match', ['get', 'kind'],
            'shadow_fleet', 'arrow-shadow_fleet',
            'narco', 'arrow-narco',
            'iuu_fishing', 'arrow-iuu_fishing',
            'sabotage', 'arrow-sabotage',
            'smuggling', 'arrow-smuggling',
            'other', 'arrow-other',
            'arrow-region',
          ],
          'icon-size': ['+', 0.55, ['/', cappedRisk, 260]],
          'icon-rotate': ['get', 'bearing'],
          'icon-rotation-alignment': 'map',
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
      })

      map.addSource('track', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })
      map.addLayer({
        id: 'track-line',
        type: 'line',
        source: 'track',
        paint: { 'line-color': '#5bc8af', 'line-width': 1.5, 'line-opacity': 0.9 },
      })

      // selection highlight: a bright pulsing ring around the ship you picked,
      // so you can tell which one it is in a crowd
      map.addSource('selection', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: 'selection-ring',
        type: 'circle',
        source: 'selection',
        paint: {
          'circle-radius': 16, 'circle-color': 'rgba(0,0,0,0)',
          'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2.5, 'circle-stroke-opacity': 0.9,
        },
      })

      // hover tooltip with the ship name (no glyph font needed - it's an HTML
      // popup, not map text). Only for named vessel/flagged layers.
      // Hover-capable pointers only: touch browsers emulate mousemove on tap
      // but never fire mouseleave, which left the tooltip stuck to the map.
      // Tapping already opens the info panel, so phones lose nothing.
      const canHover = window.matchMedia('(hover: hover)').matches
      const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 })
      if (canHover) for (const layer of ['vessels-dots', 'vessels-arrows']) {
        map.on('mousemove', layer, (e) => {
          map.getCanvas().style.cursor = 'pointer'
          const f = e.features?.[0]
          if (!f) return
          const name = f.properties?.name
          const risk = f.properties?.risk
          const rules = f.properties?.rules
          const shipType = f.properties?.shipType
          const label = `<b>${name}</b>${shipType ? ` · ${shipType}` : ''}` +
            (risk ? ` · risk ${Math.round(risk)}` : '') +
            (rules ? `<br><span style="color:#9fb3c8">${rules}</span>` : '')
          hover.setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
            .setHTML(label).addTo(map)
        })
        map.on('mouseleave', layer, () => {
          map.getCanvas().style.cursor = ''
          hover.remove()
        })
      }
      // ambient ships: lightweight name tooltip so they can be identified too
      if (canHover) for (const layer of ['world-dots', 'world-arrows']) {
        map.on('mousemove', layer, (e) => {
          map.getCanvas().style.cursor = 'pointer'
          const f = e.features?.[0]
          const name = f?.properties?.name
          if (!name) { hover.remove(); return }
          hover.setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
            .setHTML(`<b>${name}</b><br><span style="color:#9fb3c8">${f.properties?.moving ? 'underway' : 'stopped'}</span>`)
            .addTo(map)
        })
        map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; hover.remove() })
      }
      map.on('mouseenter', 'clusters-glow', () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', 'clusters-glow', () => (map.getCanvas().style.cursor = ''))
      setMapReady(true)
    })

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Click handlers live in their own effect so they see current data
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    // Padded hit-test: query a box around the click so small dots don't need a
    // pixel-perfect hit. Priority: watchlist ship > flagged suspect > cluster.
    const PAD = 12
    const onMapClick = (e: maplibregl.MapMouseEvent) => {
      const box: [maplibregl.PointLike, maplibregl.PointLike] = [
        [e.point.x - PAD, e.point.y - PAD], [e.point.x + PAD, e.point.y + PAD],
      ]
      const near = (layer: string) =>
        map.queryRenderedFeatures(box, { layers: [layer] })
          .map((f) => ({ f, d: Math.hypot(
            map.project((f.geometry as GeoJSON.Point).coordinates as [number, number]).x - e.point.x,
            map.project((f.geometry as GeoJSON.Point).coordinates as [number, number]).y - e.point.y) }))
          .sort((a, b) => a.d - b.d)

      const clearAll = () => { setSelectedCluster(null); setSelectedAmbient(null) }
      const ship = [...near('vessels-dots'), ...near('vessels-arrows')].sort((a, b) => a.d - b.d)[0]
      if (ship) {
        const pos = positions?.find((p) => p.mmsi === ship.f.properties?.mmsi)
        if (pos) { clearAll(); setSelected(pos); return }
      }
      const cl = near('clusters-glow')[0]
      if (cl) {
        const lat = (cl.f.geometry as GeoJSON.Point).coordinates[1]
        const c = clusters?.find((x) => Math.abs(x.lat - lat) < 0.01)
        if (c) { setSelected(null); setSelectedAmbient(null); setSelectedCluster(c); return }
      }
      // any ambient ship: fetch its info and show a panel
      const amb = [...near('world-dots'), ...near('world-arrows')].sort((a, b) => a.d - b.d)[0]
      if (amb) {
        const mmsi = amb.f.properties?.mmsi as number
        const coords = (amb.f.geometry as GeoJSON.Point).coordinates
        // the live feed often knows the name before our registry does - keep it
        const liveName = amb.f.properties?.name as string | undefined
        api<Omit<AmbientInfo, 'lat' | 'lon' | 'sog' | 'bearing'>>(`/vessels/${mmsi}/info`)
          .then((info) => {
            clearAll(); setSelected(null)
            const name = info.name ?? (liveName && liveName !== String(mmsi) ? liveName : null)
            setSelectedAmbient({ ...info, name, lat: coords[1], lon: coords[0],
              sog: (amb.f.properties?.sog as number | undefined) ?? null,
              bearing: (amb.f.properties?.bearing as number | undefined) ?? null })
          }).catch(() => {})
      }
    }
    map.on('click', onMapClick)
    return () => { map.off('click', onMapClick) }
  }, [mapReady, positions, clusters])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !positions) return
    ;(map.getSource('vessels') as GeoJSONSource)?.setData(vesselsToGeoJSON(positions))
  }, [mapReady, positions])

  // put the highlight ring on whichever ship is currently selected, and pulse it
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    // place the ring on the ship's latest REAL position: look it up in the
    // freshest polled data, falling back to the spot captured at click time.
    // (No dead-reckoning - we only ever show positions we actually received.)
    let sel: [number, number] | null = null
    if (selected) {
      const live = positions?.find((p) => p.mmsi === selected.mmsi)
      sel = live ? [live.lon, live.lat] : [selected.lon, selected.lat]
    } else if (selectedAmbient) {
      const live = world?.find((p) => p.mmsi === selectedAmbient.mmsi)
      if (live) sel = [live.lon, live.lat]
      else if (selectedAmbient.lat != null && selectedAmbient.lon != null) {
        sel = [selectedAmbient.lon, selectedAmbient.lat]
      }
    }
    const src = map.getSource('selection') as GeoJSONSource | undefined
    if (!src) return
    src.setData(sel
      ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: sel }, properties: {} }] }
      : { type: 'FeatureCollection', features: [] })
    if (!sel) return
    let t = 0
    const id = window.setInterval(() => {
      t += 0.15
      map.setPaintProperty('selection-ring', 'circle-radius', 15 + Math.sin(t) * 5)
      map.setPaintProperty('selection-ring', 'circle-stroke-opacity', 0.6 + Math.sin(t) * 0.35)
    }, 60)
    return () => window.clearInterval(id)
  }, [mapReady, selected, selectedAmbient, positions, world])

  // score slider: hide watchlist ships below the chosen risk. Ambient
  // traffic (risk 0) always stays as context.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    const meets: maplibregl.ExpressionSpecification = ['>=', ['get', 'risk'], minRisk]
    map.setFilter('vessels-dots', ['all', ['get', 'watchlist'], ['==', ['get', 'moving'], 0], meets])
    map.setFilter('vessels-arrows', ['all', ['get', 'watchlist'], ['==', ['get', 'moving'], 1], meets])
    map.setFilter('vessels-halo', ['all', ['get', 'watchlist'], ['>', ['get', 'risk'], 0], meets])
  }, [mapReady, minRisk])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !world) return
    ;(map.getSource('world') as GeoJSONSource)?.setData({
      type: 'FeatureCollection',
      features: world.map((p) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
        properties: {
          mmsi: p.mmsi,
          moving: (p.sog ?? 0) > MOVING_SOG ? 1 : 0,
          bearing: p.cog ?? 0,
          sog: p.sog ?? 0,
          name: p.ship_name ?? String(p.mmsi),
        },
      })),
    })
  }, [mapReady, world])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !regions) return
    ;(map.getSource('regions') as GeoJSONSource)?.setData(regionsToGeoJSON(regions))
  }, [mapReady, regions])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !clusters) return
    ;(map.getSource('clusters') as GeoJSONSource)?.setData({
      type: 'FeatureCollection',
      features: clusters.map((c) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [c.lon, c.lat] },
        properties: { count: c.count },
      })),
    })
  }, [mapReady, clusters])

  // Satellite cross-checks for the selected watchlist vessel - whichever
  // panel it opened in (live feed or ambient/deep-link)
  const checksMmsi = selected?.category ? selected.mmsi
    : selectedAmbient?.on_watchlist ? selectedAmbient.mmsi : null
  useEffect(() => {
    setPosChecks([])
    if (checksMmsi == null) return
    let cancelled = false
    api<PositionCheck[]>(`/vessels/${checksMmsi}/position-checks`)
      .then((c) => { if (!cancelled) setPosChecks(c) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [checksMmsi])

  // Fetch and draw the selected vessel's track - for ANY selected ship
  // (watchlist or ambient/world), since we now store
  // history for every ship. Ambient ships just started recording, so their
  // tail grows over time (1 stored position per minute).
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    const source = map.getSource('track') as GeoJSONSource
    const mmsi = selected?.mmsi ?? selectedAmbient?.mmsi
    if (mmsi == null) {
      source?.setData({ type: 'FeatureCollection', features: [] })
      return
    }
    api<TrackPoint[]>(`/vessels/${mmsi}/track?hours=72`).then((track) => {
      const coords: [number, number][] = track.map((p) => [p.lon, p.lat])
      source?.setData({
        type: 'FeatureCollection',
        features: coords.length > 1 ? [{
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: coords },
          properties: {},
        }] : [],
      })
    }).catch(() => source?.setData({ type: 'FeatureCollection', features: [] }))
  }, [mapReady, selected, selectedAmbient])

  return (
    <>
      <div ref={containerRef} className="map-container" />

      {selected && (
        <aside className="vessel-panel">
          <button className="close" onClick={() => setSelected(null)} aria-label="Close">✕</button>
          <h2>{selected.vessel_name ?? selected.ship_name ?? selected.mmsi}</h2>
          {selected.category && <span className={`tag ${selected.category}`}>{CATEGORY_LABELS[selected.category] ?? selected.category}</span>}
          <dl className="datagrid">
            <dt>MMSI</dt><dd>{selected.mmsi}</dd>
            {selected.ship_type && <><dt>Type</dt><dd>{selected.ship_type}</dd></>}
            <dt>Position</dt><dd>{selected.lat.toFixed(4)}, {selected.lon.toFixed(4)}</dd>
            <dt>Speed</dt><dd>{selected.sog != null ? `${selected.sog.toFixed(1)} kn` : '-'}</dd>
            <dt>Course</dt><dd>{selected.cog != null ? `${selected.cog.toFixed(0)}°` : '-'}</dd>
            <dt>Last seen</dt><dd>{new Date(selected.ts).toLocaleString('en-GB', { timeZone: 'UTC' })} UTC</dd>
            <dt>Source</dt><dd>{selected.source}</dd>
            {selected.risk_score != null && selected.risk_score > 0 && (
              <><dt>Risk score</dt><dd>{Math.round(selected.risk_score)}</dd></>
            )}
          </dl>
          <PatternTags patterns={selected.patterns ?? []} />
          <WatchNotes notes={selected.notes} />
          <SatChecks checks={posChecks} />
          <FollowButton mmsi={selected.mmsi} followed={followed} onAdded={addFollowed} />
          <ShareButton mmsi={selected.mmsi} />
        </aside>
      )}

      {selectedAmbient && (
        <aside className="vessel-panel">
          <button className="close" onClick={() => setSelectedAmbient(null)} aria-label="Close">✕</button>
          <h2>{selectedAmbient.name ?? selectedAmbient.mmsi}</h2>
          {selectedAmbient.category
            ? <span className={`tag ${selectedAmbient.category}`}>{CATEGORY_LABELS[selectedAmbient.category] ?? selectedAmbient.category}</span>
            : <span className="tag closed">{selectedAmbient.on_watchlist ? 'on watchlist' : 'ordinary traffic'}</span>}
          <dl className="datagrid">
            <dt>MMSI</dt><dd>{selectedAmbient.mmsi}</dd>
            {selectedAmbient.imo && <><dt>IMO</dt><dd>{selectedAmbient.imo}</dd></>}
            {selectedAmbient.ship_type && <><dt>Type</dt><dd>{selectedAmbient.ship_type}</dd></>}
            {selectedAmbient.flag && <><dt>Flag</dt><dd>{selectedAmbient.flag}</dd></>}
            {selectedAmbient.destination && <><dt>Destination</dt><dd>{selectedAmbient.destination}</dd></>}
            {selectedAmbient.lat != null && selectedAmbient.lon != null && (
              <><dt>Position</dt><dd>{selectedAmbient.lat.toFixed(4)}, {selectedAmbient.lon.toFixed(4)}</dd></>
            )}
            {selectedAmbient.sog != null && <><dt>Speed</dt><dd>{selectedAmbient.sog.toFixed(1)} kn</dd></>}
            {selectedAmbient.bearing != null && <><dt>Course</dt><dd>{selectedAmbient.bearing.toFixed(0)}°</dd></>}
            {selectedAmbient.last_pos && (
              <><dt>Last seen</dt><dd>{new Date(selectedAmbient.last_pos.ts).toLocaleString('en-GB', { timeZone: 'UTC' })} UTC</dd>
              <dt>Source</dt><dd>{selectedAmbient.last_pos.source}</dd></>
            )}
            {selectedAmbient.risk_score != null && selectedAmbient.risk_score > 0 && (
              <><dt>Risk score</dt><dd>{Math.round(selectedAmbient.risk_score)}</dd></>
            )}
          </dl>
          <PatternTags patterns={selectedAmbient.patterns ?? []} />
          <WatchNotes notes={selectedAmbient.notes} />
          <SatChecks checks={posChecks} />
          {!selectedAmbient.on_watchlist && (
            <p style={{ marginTop: '0.7rem', fontSize: 12, color: 'var(--muted)' }}>
              Not flagged - no sanctions match or suspicious pattern detected. It only
              gets a track and behavioural checks once it enters a monitored region.
            </p>
          )}
          <FollowButton mmsi={selectedAmbient.mmsi} followed={followed} onAdded={addFollowed} />
          <ShareButton mmsi={selectedAmbient.mmsi} />
        </aside>
      )}

      {selectedCluster && (
        <aside className="vessel-panel">
          <button className="close" onClick={() => setSelectedCluster(null)} aria-label="Close">✕</button>
          <h2>{selectedCluster.count} shadow-fleet ships anchored together</h2>
          <span className="tag open">possible ship-to-ship area</span>
          <div className="mono" style={{ color: 'var(--muted)', fontSize: 12, marginTop: '0.3rem' }}>
            {selectedCluster.sanctioned} under OFAC/EU/UK sanctions
            {' · '}{selectedCluster.count - selectedCluster.sanctioned} on shadow-fleet lists
          </div>
          <dl className="datagrid">
            {selectedCluster.region && <><dt>Where</dt><dd>{selectedCluster.region}</dd></>}
            <dt>Centre</dt><dd>{selectedCluster.lat.toFixed(3)}, {selectedCluster.lon.toFixed(3)}</dd>
            <dt>Anchored</dt><dd>{selectedCluster.count}</dd>
            {selectedCluster.nearby > 0 && (
              <><dt>In the area</dt><dd>≈{selectedCluster.count + selectedCluster.nearby} flagged ships</dd></>
            )}
          </dl>
          <div className="panel-detail" style={{ marginTop: '0.7rem' }}>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--muted)', marginBottom: '0.3rem' }}>
              Why ships meet here
            </div>
            <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text)' }}>
              {selectedCluster.region_kind === 'sts'
                ? 'This is a known anchoring and ship-to-ship transfer zone: tankers wait here for orders, transfer cargo between hulls, and re-document its origin.'
                : selectedCluster.region_kind === 'transit'
                  ? 'This is a transit corridor, not a normal anchorage - several flagged ships holding position together here usually means waiting for a rendezvous or orders.'
                  : 'Open water outside our monitored regions - several flagged ships holding position together is itself the ship-to-ship staging signature.'}
            </div>
          </div>
          {selectedCluster.recent_alerts.length > 0 && (
            <div className="panel-detail" style={{ marginTop: '0.7rem' }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--muted)', marginBottom: '0.35rem' }}>
                Alerts here - last 72h
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
                {selectedCluster.recent_alerts.map((a) => (
                  <span key={a.pattern} className="tag open">
                    {a.pattern}{a.count > 1 ? ` ×${a.count}` : ''}
                  </span>
                ))}
              </div>
            </div>
          )}
          <div style={{ marginTop: '0.7rem' }}>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--muted)', marginBottom: '0.35rem' }}>
              Members
            </div>
            <div style={{ maxHeight: 260, overflowY: 'auto', display: 'grid', gap: '0.25rem' }}>
              {selectedCluster.members.map((m) => (
                <button key={m.mmsi} className="cluster-member"
                  onClick={() => { setSelectedCluster(null); selectByMmsi(m.mmsi) }}>
                  <span>{m.name ?? m.mmsi}</span>
                  {m.category && <span className={`tag ${m.category}`}>{CATEGORY_LABELS[m.category] ?? m.category}</span>}
                </button>
              ))}
            </div>
          </div>
        </aside>
      )}

      <div className="ship-search">
        <input type="text" placeholder="Search ship name, MMSI or IMO..."
          value={query} onChange={(e) => setQuery(e.target.value)} />
        {results.length > 0 && (
          <div className="search-results">
            {results.map((h) => (
              <button key={h.mmsi} onClick={() => openHit(h)}>
                <span>{h.name ?? h.mmsi}{h.category && <span className={`tag ${h.category}`} style={{ marginLeft: 6 }}>{h.category}</span>}</span>
                <span className="mono">{h.mmsi}{h.ship_type ? ` · ${h.ship_type}` : ''}{h.lat == null ? ' · no position' : h.live ? '' : ' · last known'}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="risk-slider">
        <label htmlFor="risk">Show risk ≥ <b>{minRisk}</b></label>
        <input id="risk" type="range" min={0} max={150} step={5}
          value={minRisk} onChange={(e) => setMinRisk(Number(e.target.value))} />
        <span className="risk-count">
          {positions ? positions.filter((p) => p.category && (p.risk_score ?? 0) >= minRisk).length : 0} watchlist ships shown
        </span>
      </div>

      {/* on phones the two bottom panels start collapsed behind these chips
          (they'd otherwise cover the whole map); desktop hides the chips and
          shows the panels as always */}
      <div className="panel-toggles">
        <button className={`panel-toggle${showLegend ? ' on' : ''}`}
                onClick={() => { setShowLegend((v) => !v); setShowClusters(false) }}>
          Legend
        </button>
        {clusters && clusters.length > 0 && (
          <button className={`panel-toggle${showClusters ? ' on' : ''}`}
                  onClick={() => { setShowClusters((v) => !v); setShowLegend(false) }}>
            Gatherings
          </button>
        )}
      </div>

      {clusters && clusters.length > 0 && (
        <div className={`cluster-panel${showClusters ? ' open' : ''}`}>
          <div className="legend-head" style={{ marginTop: 0 }}>Fleet gathering spots</div>
          {clusters.slice(0, 5).map((c, i) => (
            <button key={i} className="cluster-row"
              onClick={() => {
                setSelected(null); setSelectedAmbient(null); setSelectedCluster(c)
                mapRef.current?.flyTo({ center: [c.lon, c.lat], zoom: 10 })
              }}>
              <b>{c.count} shadow-fleet ships anchored</b>
              {c.nearby > 0 ? ` · ≈${c.count + c.nearby} flagged in the area` : ''}
              {c.region ? ` · ${c.region}` : ` · around ${c.lat.toFixed(1)}, ${c.lon.toFixed(1)}`}
              <span className="mono">
                {c.recent_alerts.length > 0
                  ? `alerts: ${c.recent_alerts.slice(0, 3).map((a) => a.pattern).join(', ')}`
                  : `${c.members.slice(0, 4).map((m) => m.name).filter(Boolean).join(', ')}${c.count > 4 ? '...' : ''}`}
                {' '}- click for details
              </span>
            </button>
          ))}
        </div>
      )}

      <div className={`legend${showLegend ? ' open' : ''}`}>
        <div className="legend-head">On a sanctions or ban list</div>
        <div><span className="swatch" style={{ background: COLORS.shadow_fleet }} />Sanctioned oil tanker</div>
        <div><span className="swatch" style={{ background: COLORS.smuggling }} />Sanctioned cargo / contraband</div>
        <div><span className="swatch" style={{ background: COLORS.sabotage }} />Cable &amp; infrastructure attacks</div>

        <div className="legend-head">Flagged by behaviour</div>
        <div><span className="swatch" style={{ background: COLORS.narco }} />Drug trafficking</div>
        <div><span className="swatch" style={{ background: COLORS.iuu_fishing }} />Illegal fishing</div>
        <div><span className="swatch" style={{ background: COLORS.other }} />Behaviour flag (click ship for pattern)</div>

        <div className="legend-head">Markers</div>
        <div><span className="swatch" style={{ background: COLORS.region }} />Ordinary traffic</div>
        <div className="legend-note">▲ underway (points where it is heading) · ● stopped · size = risk</div>
      </div>
    </>
  )
}
