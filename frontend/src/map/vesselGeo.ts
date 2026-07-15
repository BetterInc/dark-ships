import type { LatestPosition } from '../api/types'

export const MOVING_SOG = 1.0 // above this a ship is underway -> arrow; else -> dot

// Shared between the map page and the snapshot worker, so the GeoJSON is
// built off the main thread but rendered with identical semantics.
export function vesselsToGeoJSON(positions: LatestPosition[]): GeoJSON.FeatureCollection {
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
