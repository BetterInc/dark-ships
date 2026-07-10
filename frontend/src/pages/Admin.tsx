import { FormEvent, Fragment, useEffect, useState } from 'react'
import { api, usePolling } from '../api/client'
import Pagination from '../components/Pagination'
import { useAuth } from '../auth/AuthContext'

const PAGE_SIZE = 25

interface AdminUser {
  id: number
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
  watchlist_count: number
}

interface WatchPosition {
  lat: number
  lon: number
  ts: string
  live: boolean
}

interface WatchItem {
  mmsi: number
  name: string | null
  ship_type: string | null
  note: string | null
  position: WatchPosition | null
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

export default function Admin() {
  const { user: me } = useAuth()
  const { data: users, error, refresh } = usePolling<AdminUser[]>('/admin/users', 60_000)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // per-user expanded watchlist: undefined = collapsed, null = loading
  const [openLists, setOpenLists] = useState<Record<number, WatchItem[] | null>>({})

  const filtered = (users ?? []).filter(
    (u) => !query.trim() || u.email.toLowerCase().includes(query.trim().toLowerCase())
  )
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  useEffect(() => { if (page > pageCount - 1) setPage(pageCount - 1) }, [page, pageCount])
  const pageItems = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)

  async function addUser(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setBusy(true)
    setActionError(null)
    try {
      await api('/admin/users', {
        method: 'POST',
        body: JSON.stringify({ email: email.trim(), password }),
      })
      setEmail('')
      setPassword('')
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function patchUser(id: number, fields: Partial<Pick<AdminUser, 'is_active' | 'is_superuser'>>) {
    setBusy(true)
    setActionError(null)
    try {
      await api(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(fields) })
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function toggleWatchlist(id: number) {
    if (id in openLists) {
      setOpenLists(({ [id]: _closed, ...rest }) => rest)
      return
    }
    setOpenLists((prev) => ({ ...prev, [id]: null }))
    try {
      const items = await api<WatchItem[]>(`/admin/users/${id}/watchlist`)
      setOpenLists((prev) => (id in prev ? { ...prev, [id]: items } : prev))
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
      setOpenLists(({ [id]: _failed, ...rest }) => rest)
    }
  }

  return (
    <div className="page">
      <h1>Admin</h1>
      <p className="sub">
        Manage accounts: create users, suspend access, grant admin rights and
        inspect a user's watchlist.
      </p>

      <form className="filters" onSubmit={addUser}>
        <label className="field">
          Email
          <input
            type="email"
            required
            placeholder="user@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="field">
          Password
          <input
            type="password"
            required
            minLength={8}
            placeholder="min. 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button className="primary" disabled={busy}>Add user</button>
      </form>

      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label className="field">
          Search
          <input
            placeholder="filter by email"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(0) }}
          />
        </label>
        <span className="filter-count">{filtered.length} users</span>
      </form>

      {(actionError || error) && <p className="error">{actionError ?? error}</p>}

      <Pagination page={page} pageCount={pageCount} total={filtered.length} pageSize={PAGE_SIZE} onPage={setPage} />
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Status</th>
              <th>Watchlist</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((u) => {
              const self = me != null && String(u.id) === String(me.id)
              const list = openLists[u.id]
              return (
                <Fragment key={u.id}>
                  <tr>
                    <td className="mono">{u.email}{self && ' (you)'}</td>
                    <td>
                      {u.is_active
                        ? <span className="tag other">active</span>
                        : <span className="tag open">suspended</span>}
                      {u.is_superuser && <span className="tag shadow_fleet">admin</span>}
                      {!u.is_verified && <span className="mywatch-seen"> unverified</span>}
                    </td>
                    <td className="mono">
                      {u.watchlist_count}{' '}
                      {u.watchlist_count > 0 && (
                        <button className="ghost" onClick={() => toggleWatchlist(u.id)}>
                          {u.id in openLists ? 'Hide' : 'View'}
                        </button>
                      )}
                    </td>
                    <td>
                      <button
                        className="ghost"
                        disabled={busy || self}
                        title={self ? 'You cannot suspend your own account' : undefined}
                        onClick={() => patchUser(u.id, { is_active: !u.is_active })}
                      >
                        {u.is_active ? 'Suspend' : 'Activate'}
                      </button>{' '}
                      <button
                        className="ghost"
                        disabled={busy || (self && u.is_superuser)}
                        title={self && u.is_superuser ? 'You cannot de-admin your own account' : undefined}
                        onClick={() => patchUser(u.id, { is_superuser: !u.is_superuser })}
                      >
                        {u.is_superuser ? 'Remove admin' : 'Make admin'}
                      </button>
                    </td>
                  </tr>
                  {u.id in openLists && (
                    <tr>
                      <td colSpan={4}>
                        {list == null ? (
                          <span className="mywatch-seen">Loading watchlist...</span>
                        ) : list.length === 0 ? (
                          <span className="mywatch-seen">Watchlist is empty.</span>
                        ) : (
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>MMSI</th>
                                <th>Ship</th>
                                <th>Type</th>
                                <th>Last seen</th>
                                <th>Note</th>
                              </tr>
                            </thead>
                            <tbody>
                              {list.map((v) => (
                                <tr key={v.mmsi}>
                                  <td className="mono">{v.mmsi}</td>
                                  <td>{v.name ?? '-'}</td>
                                  <td>{v.ship_type ?? '-'}</td>
                                  <td className="mono">
                                    {v.position ? (v.position.live ? 'live' : relativeTime(v.position.ts)) : 'not seen yet'}
                                  </td>
                                  <td>{v.note ?? '-'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageCount={pageCount} total={filtered.length} pageSize={PAGE_SIZE} onPage={setPage} />
    </div>
  )
}
