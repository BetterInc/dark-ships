export type Category =
  | 'shadow_fleet'   // sanctions-evading oil transport (Russia, Iran, Venezuela, North Korea)
  | 'narco'          // drug trafficking
  | 'iuu_fishing'    // illegal, unreported and unregulated fishing
  | 'sabotage'       // infrastructure sabotage (cables, pipelines) / spy ships
  | 'smuggling'      // arms, contraband, waste dumping
  | 'other'

export const CATEGORY_LABELS: Record<string, string> = {
  shadow_fleet: 'shadow fleet',
  narco: 'narco',
  iuu_fishing: 'IUU fishing',
  sabotage: 'sabotage',
  smuggling: 'smuggling',
  other: 'other',
}

export interface Vessel {
  id: number
  mmsi: number
  imo: string | null
  name: string | null
  flag: string | null
  category: Category
  notes: string | null
  active: boolean
  pinned: boolean
  auto_added: boolean
  followed: boolean
  risk_score: number
  created_at: string
}

export interface SuggestionEvent {
  rule: string
  ts: string
  score: number
  details: Record<string, unknown>
}

export interface Suggestion {
  mmsi: number
  imo: string | null
  name: string | null
  score: number
  on_watchlist: boolean
  last_seen: string | null
  lat: number | null
  lon: number | null
  events: SuggestionEvent[]
}

export interface PositionCheck {
  id: number
  mmsi: number
  claimed_ts: string
  claimed_lat: number
  claimed_lon: number
  source: 'sentinel-1' | 'sentinel-2'
  product_name: string | null
  acquired_at: string
  delta_minutes: number
  quicklook_url: string | null
  browser_url: string | null
  // automated SAR detection (null = not analyzed / no verdict possible)
  hull_detected: boolean | null
  target_count: number | null
  nearest_offset_m: number | null
  // target also bright on a pass weeks earlier: fixed structure or long-anchored
  persistent_target: boolean | null
  // measured target size and whether it can be this ship (per AIS dimensions)
  target_length_m: number | null
  size_match: boolean | null
  chip_key: string | null  // set = radar chip PNG at /api/position-checks/{id}/chip
  optical_chip_key: string | null  // set = true-colour Sentinel-2 companion (?kind=optical)
}

export interface LatestPosition {
  mmsi: number
  ts: string
  lat: number
  lon: number
  sog: number | null
  cog: number | null
  heading: number | null
  nav_status: number | null
  ship_name: string | null
  source: 'watchlist' | 'region'
  category: Category | null
  vessel_name: string | null
  risk_score: number | null
  patterns: string[]
  notes: string | null
  ship_type: string | null
}

export interface RiskEventFeedItem {
  mmsi: number
  vessel_name: string
  rule: string
  label: string
  severity: 'high' | 'medium' | 'low'
  score: number
  ts: string
  detail: Record<string, unknown>
}

export interface Cluster {
  lat: number
  lon: number
  count: number
  nearby: number      // other flagged ships within the cluster radius (any category)
  sanctioned: number  // how many are on an actual government sanctions list
  region: string | null       // monitored region the centre falls in, if any
  region_kind: string | null  // "sts" (anchoring normal) | "transit" (dwelling suspicious)
  recent_alerts: { pattern: string; count: number }[]  // behaviour alerts fired here (72h)
  members: { mmsi: number; name: string | null; category: Category | null }[]
}

export interface WorldPosition {
  mmsi: number
  ts: string
  lat: number
  lon: number
  sog: number | null
  cog: number | null
  ship_name: string | null
}

export interface TrackPoint {
  mmsi: number
  ts: string
  lat: number
  lon: number
  sog: number | null
}

export interface Region {
  name: string
  bbox: [[number, number], [number, number]]
}
