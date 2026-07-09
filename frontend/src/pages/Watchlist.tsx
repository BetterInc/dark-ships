import { FormEvent, useEffect, useState } from 'react'
import { api, usePolling } from '../api/client'
import Pagination from '../components/Pagination'
import { CATEGORY_LABELS } from '../api/types'

const FREE_LIMIT = 10
const PAGE_SIZE = 25

interface WatchPosition {
  lat: number
  lon: number
  sog: number | null
  cog: number | null
  ts: string
  live: boolean
}

interface WatchRisk {
  category: string | null
  risk_score: number | null
  on_suggestions: boolean
}

interface WatchItem {
  mmsi: number
  name: string | null
  ship_type: string | null
  note: string | null
  created_at: string
  position: WatchPosition | null
  risk: WatchRisk | null
}

/** Short relative "last seen" label from an ISO timestamp. */
function relativeTime(ts: string): string {
  const then = new Date(ts).getTime()
  if (Number.isNaN(then)) return ts
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

function fmtCoord(lat: number, lon: number): string {
  const la = `${Math.abs(lat).toFixed(3)}${lat >= 0 ? 'N' : 'S'}`
  const lo = `${Math.abs(lon).toFixed(3)}${lon >= 0 ? 'E' : 'W'}`
  return `${la} ${lo}`
}

export default function Watchlist() {
  const { data: items, error, refresh } = usePolling<WatchItem[]>('/me/watchlist', 60_000)
  const [mmsiInput, setMmsiInput] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const count = items?.length ?? 0
  const atCap = count >= FREE_LIMIT
  const pageCount = Math.max(1, Math.ceil(count / PAGE_SIZE))
  useEffect(() => { if (page > pageCount - 1) setPage(pageCount - 1) }, [page, pageCount])
  const pageItems = (items ?? []).slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)
  const pageMmsis = pageItems.map((v) => v.mmsi)
  const allPageSelected = pageMmsis.length > 0 && pageMmsis.every((m) => selected.has(m))

  function toggle(mmsi: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(mmsi) ? next.delete(mmsi) : next.add(mmsi)
      return next
    })
  }

  function toggleSelectAllPage() {
    setSelected((prev) => {
      const next = new Set(prev)
      if (pageMmsis.every((m) => next.has(m))) pageMmsis.forEach((m) => next.delete(m))
      else pageMmsis.forEach((m) => next.add(m))
      return next
    })
  }

  async function removeSelected() {
    const targets = [...selected]
    if (!targets.length) return
    setBusy(true)
    try {
      for (const mmsi of targets) {
        await api(`/me/watchlist/${mmsi}`, { method: 'DELETE' })
      }
      setAddError(null)
      setSelected(new Set())
      await refresh()
    } catch (err) {
      setAddError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function addShip(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const mmsi = Number(mmsiInput.trim())
    if (!mmsi) {
      setAddError('Enter a 9-digit MMSI.')
      return
    }
    setBusy(true)
    setAddError(null)
    try {
      await api('/me/watchlist', {
        method: 'POST',
        body: JSON.stringify({ mmsi }),
      })
      setMmsiInput('')
      await refresh()
    } catch (err) {
      setAddError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function saveNote(mmsi: number, current: string | null, value: string) {
    const next = value.trim()
    const orig = (current ?? '').trim()
    if (next === orig) return
    try {
      await api(`/me/watchlist/${mmsi}`, {
        method: 'PATCH',
        body: JSON.stringify({ note: next || null }),
      })
      setAddError(null)
      await refresh()
    } catch (err) {
      setAddError(err instanceof Error ? err.message : String(err))
    }
  }

  async function removeShip(mmsi: number) {
    try {
      await api(`/me/watchlist/${mmsi}`, { method: 'DELETE' })
      setAddError(null)
      await refresh()
    } catch (err) {
      setAddError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="page">
      <h1>My watchlist</h1>
      <p className="sub">
        Your private list of ships to keep an eye on. Follow up to {FREE_LIMIT} vessels
        by MMSI and see their latest known position and risk here. Nobody else can see
        this list.
      </p>

      <form className="filters mywatch-add" onSubmit={addShip}>
        <label className="field">
          Add by MMSI
          <input
            name="mmsi"
            inputMode="numeric"
            pattern="[0-9]{9}"
            placeholder="244123456"
            value={mmsiInput}
            onChange={(e) => setMmsiInput(e.target.value)}
            disabled={atCap}
          />
        </label>
        <button className="primary" disabled={busy || atCap}>Add ship</button>
        {atCap && (
          <span className="mywatch-caphint mono">
            Free plan is limited to {FREE_LIMIT} ships. Remove one to add another.
          </span>
        )}
        <span className="filter-count">{count} / {FREE_LIMIT} followed</span>
      </form>

      {(addError || error) && <p className="error">{addError ?? error}</p>}

      {items && count === 0 ? (
        <div className="mywatch-empty">
          <p>You are not following any ships yet.</p>
          <p className="sub" style={{ margin: '0.5rem 0 0' }}>
            Add a ship by its MMSI above, or find ships to follow on the{' '}
            <a href="/suggestions">Suggestions</a> page and the{' '}
            <a href="/">Map</a>, then add them here.
          </p>
        </div>
      ) : (
        <>
        <div className="sug-toolbar">
          <span className="mono">{selected.size} selected</span>
          <button className="danger" disabled={busy || selected.size === 0} onClick={removeSelected}>
            Remove selected
          </button>
          {selected.size > 0 && (
            <button className="ghost" onClick={() => setSelected(new Set())}>Clear</button>
          )}
        </div>
        <Pagination page={page} pageCount={pageCount} total={count} pageSize={PAGE_SIZE} onPage={setPage} />
        <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th className="col-check">
                <input
                  type="checkbox"
                  className="sug-check"
                  checked={allPageSelected}
                  onChange={toggleSelectAllPage}
                  aria-label="Select all on page"
                />
              </th>
              <th>Ship</th>
              <th>MMSI</th>
              <th>Type</th>
              <th>Position</th>
              <th>Risk</th>
              <th>Note</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {pageItems.map((v) => (
              <tr key={v.mmsi} className={selected.has(v.mmsi) ? 'row-selected' : ''}>
                <td className="col-check">
                  <input
                    type="checkbox"
                    className="sug-check"
                    checked={selected.has(v.mmsi)}
                    onChange={() => toggle(v.mmsi)}
                    aria-label={`Select ${v.name ?? v.mmsi}`}
                  />
                </td>
                <td>{v.name ?? <span className="mono">{v.mmsi}</span>}</td>
                <td className="mono">{v.mmsi}</td>
                <td>{v.ship_type ?? '-'}</td>
                <td className="mywatch-pos">
                  {v.position ? (
                    <>
                      <span className="mono mywatch-coord">{fmtCoord(v.position.lat, v.position.lon)}</span>
                      {v.position.live ? (
                        <span className="mywatch-live">
                          <span className="mywatch-dot" /> live
                        </span>
                      ) : (
                        <span className="mywatch-seen mono">{relativeTime(v.position.ts)}</span>
                      )}
                    </>
                  ) : (
                    <span className="mywatch-seen">not seen yet</span>
                  )}
                </td>
                <td>
                  {v.risk && (v.risk.category || v.risk.risk_score != null) ? (
                    <span className="mywatch-risk">
                      {v.risk.category && (
                        <span className={`tag ${v.risk.category}`}>
                          {CATEGORY_LABELS[v.risk.category] ?? v.risk.category}
                        </span>
                      )}
                      {v.risk.risk_score != null && (
                        <span className="mono mywatch-score">{Math.round(v.risk.risk_score)}</span>
                      )}
                    </span>
                  ) : (
                    <span className="mywatch-seen">-</span>
                  )}
                </td>
                <td className="mywatch-note">
                  <input
                    className="mywatch-note-input"
                    defaultValue={v.note ?? ''}
                    placeholder="Add a note"
                    aria-label={`Note for ${v.name ?? v.mmsi}`}
                    onBlur={(e) => saveNote(v.mmsi, v.note, e.target.value)}
                  />
                </td>
                <td>
                  <button className="ghost" onClick={() => removeShip(v.mmsi)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
</div>
        <Pagination page={page} pageCount={pageCount} total={count} pageSize={PAGE_SIZE} onPage={setPage} />
        </>
      )}
    </div>
  )
}
