import { useEffect, useRef, useState } from 'react'
import type { LatestPosition } from '../api/types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''
const IDB_NAME = 'darkships'
const IDB_STORE = 'snapshots'
const IDB_KEY = 'latest.bin'

function idbOpen(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(IDB_STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function idbGet(key: string): Promise<ArrayBuffer | undefined> {
  const db = await idbOpen()
  return new Promise((resolve) => {
    const req = db.transaction(IDB_STORE).objectStore(IDB_STORE).get(key)
    req.onsuccess = () => resolve(req.result as ArrayBuffer | undefined)
    req.onerror = () => resolve(undefined)
  })
}

async function idbPut(key: string, value: ArrayBuffer): Promise<void> {
  const db = await idbOpen()
  return new Promise((resolve) => {
    const tx = db.transaction(IDB_STORE, 'readwrite')
    tx.objectStore(IDB_STORE).put(value, key)
    tx.oncomplete = () => resolve()
    tx.onerror = () => resolve()
  })
}

/** Live fleet snapshot, engineered for a reactive map:
 *  - the last snapshot paints instantly from IndexedDB on page load;
 *  - a Web Worker downloads + decodes the binary snapshot and builds the
 *    GeoJSON off the main thread (65k ships, zero UI jank);
 *  - refreshes every `intervalMs` (live data cadence). */
export function useLatestPositions(intervalMs = 90_000) {
  const [positions, setPositions] = useState<LatestPosition[] | null>(null)
  const [geojson, setGeojson] = useState<GeoJSON.FeatureCollection | null>(null)
  const workerRef = useRef<Worker | null>(null)

  useEffect(() => {
    const worker = new Worker(
      new URL('../workers/latest.worker.ts', import.meta.url), { type: 'module' })
    workerRef.current = worker
    let gotFresh = false

    worker.onmessage = (e: MessageEvent<{
      positions?: LatestPosition[]
      fc?: GeoJSON.FeatureCollection
      buffer?: ArrayBuffer
      error?: string
    }>) => {
      if (e.data.error || !e.data.positions || !e.data.fc) return
      // a warm-start (cached) result must never overwrite fresher live data
      if (e.data.buffer) {
        gotFresh = true
        void idbPut(IDB_KEY, e.data.buffer)
      } else if (gotFresh) {
        return
      }
      setPositions(e.data.positions)
      setGeojson(e.data.fc)
    }

    // warm start from the previous session's snapshot, then go live
    void idbGet(IDB_KEY).then((cached) => {
      if (cached && !gotFresh) worker.postMessage({ buffer: cached })
    })
    const refresh = () => worker.postMessage({ url: `${API_BASE}/api/positions/latest.bin` })
    refresh()
    const timer = window.setInterval(refresh, intervalMs)
    return () => { window.clearInterval(timer); worker.terminate() }
  }, [intervalMs])

  return { positions, geojson }
}
