import { useEffect, useState } from 'react'
import { api, usePolling } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import Pagination from '../components/Pagination'
import { TYPE_OPTIONS, ruleLabel, typeBucket } from '../api/labels'
import type { Suggestion } from '../api/types'

// Load the set of mmsis already on the logged-in user's watchlist, so ships
// already followed are shown as such instead of being offered again.
function useFollowed(): { followed: Set<number>; addFollowed: (mmsi: number) => void } {
  const { user } = useAuth()
  const [followed, setFollowed] = useState<Set<number>>(new Set())
  useEffect(() => {
    if (!user) { setFollowed(new Set()); return }
    let cancelled = false
    api<{ mmsi: number }[]>('/me/watchlist')
      .then((list) => { if (!cancelled) setFollowed(new Set(list.map((w) => w.mmsi))) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [user])
  const addFollowed = (mmsi: number) => setFollowed((prev) => new Set(prev).add(mmsi))
  return { followed, addFollowed }
}

function fmtUtc(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC' }) + ' UTC'
}

const FREE_LIMIT = 10
const PAGE_SIZE = 25
const RISK_OPTIONS = [0, 25, 40, 50, 80, 100]

export default function Suggestions() {
  const { data: suggestions, error } = usePolling<Suggestion[]>('/suggestions', 30_000)
  const { followed, addFollowed } = useFollowed()
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ text: string; err: boolean } | null>(null)
  const [page, setPage] = useState(0)
  const [typeFilter, setTypeFilter] = useState('all')
  const [minRisk, setMinRisk] = useState(0)

  const total = suggestions?.length ?? 0
  const all = (suggestions ?? []).filter((s) =>
    s.score >= minRisk &&
    (typeFilter === 'all' || s.events.some((e) => typeBucket(e.rule) === typeFilter)))
  const pageCount = Math.max(1, Math.ceil(all.length / PAGE_SIZE))
  useEffect(() => { setPage(0) }, [typeFilter, minRisk])
  useEffect(() => { if (page > pageCount - 1) setPage(pageCount - 1) }, [page, pageCount])
  const pageItems = all.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)
  const selectableOnPage = pageItems.filter((s) => !followed.has(s.mmsi)).map((s) => s.mmsi)
  const allPageSelected = selectableOnPage.length > 0 && selectableOnPage.every((m) => selected.has(m))

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
      if (selectableOnPage.every((m) => next.has(m))) selectableOnPage.forEach((m) => next.delete(m))
      else selectableOnPage.forEach((m) => next.add(m))
      return next
    })
  }

  async function bulkAdd() {
    setNotice(null)
    const targets = [...selected].filter((m) => !followed.has(m))
    if (!targets.length) return
    const slots = FREE_LIMIT - followed.size
    if (slots <= 0) {
      setNotice({ text: `Free plan reached its ${FREE_LIMIT}-ship limit. Remove some, or upgrade for more.`, err: true })
      return
    }
    const toAdd = targets.slice(0, slots)
    const skipped = targets.length - toAdd.length
    setBusy(true)
    let done = 0
    let capMsg: string | null = null
    try {
      for (const mmsi of toAdd) {
        try {
          await api('/me/watchlist', { method: 'POST', body: JSON.stringify({ mmsi }) })
          addFollowed(mmsi)
          done++
        } catch (e) {
          capMsg = e instanceof Error ? e.message : String(e)
          break
        }
      }
    } finally {
      setBusy(false)
      setSelected(new Set())
    }
    if (capMsg) setNotice({ text: capMsg, err: true })
    else if (skipped > 0) setNotice({ text: `Added ${done}. Skipped ${skipped} - free plan is limited to ${FREE_LIMIT} ships (upgrade for more).`, err: true })
    else setNotice({ text: `Added ${done} to your watchlist.`, err: false })
  }

  return (
    <div className="page">
      <h1>Suggestions <span className="mono" style={{ color: 'var(--muted)', fontSize: 13 }}>
        {followed.size}/{FREE_LIMIT} on your watchlist
      </span></h1>
      <p className="sub">
        Vessels the system has flagged - on a sanctions or IUU list, gone dark, switched
        identity, spoofing, or meeting other ships at sea. Tick ships (or "select all on
        page") and add them to your own watchlist.
      </p>

      {error && <p className="error">{error}</p>}
      {notice && (
        <p className={notice.err ? 'error' : 'sub'} style={notice.err ? undefined : { color: 'var(--watch-other)' }}>
          {notice.text}
        </p>
      )}

      {total > 0 && (
        <div className="filters">
          <label className="field">
            Type
            <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              {TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <label className="field">
            Min risk
            <select value={minRisk} onChange={(e) => setMinRisk(Number(e.target.value))}>
              {RISK_OPTIONS.map((n) => (
                <option key={n} value={n}>{n === 0 ? 'Any' : n + '+'}</option>
              ))}
            </select>
          </label>
          <span className="filter-count">{all.length} of {total} vessels</span>
        </div>
      )}

      {all.length > 0 && (
        <>
          <div className="sug-toolbar">
            <span className="mono">{selected.size} selected</span>
            <button className="primary" disabled={busy || selected.size === 0} onClick={bulkAdd}>
              Add to watchlist
            </button>
            {selected.size > 0 && (
              <button className="ghost" onClick={() => setSelected(new Set())}>Clear</button>
            )}
          </div>

          <Pagination page={page} pageCount={pageCount} total={all.length} pageSize={PAGE_SIZE} onPage={setPage} />

          <div className="table-scroll">

          <table className="data-table sug-table">
            <thead>
              <tr>
                <th className="sug-col-check">
                  <input
                    type="checkbox"
                    className="sug-check"
                    checked={allPageSelected}
                    disabled={selectableOnPage.length === 0}
                    onChange={toggleSelectAllPage}
                    aria-label="Select all on page"
                  />
                </th>
                <th>Ship</th>
                <th>MMSI</th>
                <th>IMO</th>
                <th>Risk</th>
                <th>Why</th>
                <th>Also broadcast as</th>
                <th>Seen</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((s) => {
                // "why": one tag per distinct flag. Aliases: the other names it was
                // listed under (identity changes), most useful signal, capped at 5.
                const why = [...new Set(s.events.map((e) => ruleLabel(e.rule)))]
                const aliases = [...new Set(
                  s.events
                    .map((e) => (e.details as Record<string, unknown>)['listed_as'])
                    .filter((n): n is string =>
                      typeof n === 'string' && n.toUpperCase() !== (s.name ?? '').toUpperCase())
                )].slice(0, 5)
                const onList = followed.has(s.mmsi)
                return (
                  <tr key={s.mmsi} className={onList ? 'sug-row-following' : ''}>
                    <td className="sug-col-check">
                      {onList ? (
                        <span className="sug-following mono" title="Already on your watchlist">✓</span>
                      ) : (
                        <input
                          type="checkbox"
                          className="sug-check"
                          checked={selected.has(s.mmsi)}
                          onChange={() => toggle(s.mmsi)}
                          aria-label={`Select ${s.name ?? s.mmsi}`}
                        />
                      )}
                    </td>
                    <td>{s.name ?? <span className="mono">{s.mmsi}</span>}</td>
                    <td className="mono">{s.mmsi}</td>
                    <td className="mono">{s.imo ?? '-'}</td>
                    <td><span className="tag narco">{Math.round(s.score)}</span></td>
                    <td>
                      <div className="sug-tags">
                        {why.map((w) => <span key={w} className="tag shadow_fleet">{w}</span>)}
                      </div>
                    </td>
                    <td className="sug-aliases-cell mono">
                      {aliases.length > 0 ? aliases.join(' · ') : <span className="mywatch-seen">-</span>}
                    </td>
                    <td className="mywatch-seen">{s.last_seen ? fmtUtc(s.last_seen) : 'not seen'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
</div>

          <Pagination page={page} pageCount={pageCount} total={all.length} pageSize={PAGE_SIZE} onPage={setPage} />

        </>
      )}

      {total > 0 && all.length === 0 && (
        <p className="empty">No suggestions match these filters.</p>
      )}

      {suggestions && total === 0 && (
        <p className="empty">
          Nothing flagged yet. The engine scans every 10 minutes; give the feed some
          hours to build history.
        </p>
      )}
    </div>
  )
}
