import { decodeLatestBin } from '../map/latestBinary'
import { vesselsToGeoJSON } from '../map/vesselGeo'

// Snapshot worker: downloads + decodes the 65k-ship binary snapshot and
// builds the GeoJSON OFF the main thread, so zooming/panning never stutters
// while live data refreshes. Messages:
//   in:  { url }              fetch a fresh snapshot
//   in:  { buffer }           decode a cached snapshot (IndexedDB warm start)
//   out: { positions, fc, buffer? } - buffer echoed back on fetches so the
//        page can persist it; transferred, not copied.
self.onmessage = async (e: MessageEvent<{ url?: string; buffer?: ArrayBuffer }>) => {
  try {
    let buffer = e.data.buffer
    let fetched = false
    if (!buffer && e.data.url) {
      const resp = await fetch(e.data.url)
      if (!resp.ok) throw new Error(`snapshot fetch failed: ${resp.status}`)
      buffer = await resp.arrayBuffer()
      fetched = true
    }
    if (!buffer) return
    const positions = decodeLatestBin(buffer)
    const fc = vesselsToGeoJSON(positions)
    if (fetched) {
      ;(self as unknown as Worker).postMessage({ positions, fc, buffer }, [buffer])
    } else {
      ;(self as unknown as Worker).postMessage({ positions, fc })
    }
  } catch (err) {
    ;(self as unknown as Worker).postMessage({ error: String(err) })
  }
}
