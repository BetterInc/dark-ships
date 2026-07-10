import { FormEvent, useEffect, useState } from 'react'
import { api, usePolling } from '../api/client'
import Pagination from '../components/Pagination'
import { useAuth } from '../auth/AuthContext'

const PAGE_SIZE = 25
const ROLES = ['user', 'partner', 'admin'] as const
type Role = (typeof ROLES)[number]

interface AdminUser {
  id: number
  email: string
  role: Role
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
  const [tab, setTab] = useState<'users' | 'watchlists'>('users')
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // users tab
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [newRole, setNewRole] = useState<Role>('user')
  const [activationSent, setActivationSent] = useState<Set<number>>(new Set())

  // watchlists tab
  const [watchUserId, setWatchUserId] = useState<number | null>(null)
  const [watchItems, setWatchItems] = useState<WatchItem[] | null>(null)

  const filtered = (users ?? []).filter(
    (u) => !query.trim() || u.email.toLowerCase().includes(query.trim().toLowerCase())
  )
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  useEffect(() => { if (page > pageCount - 1) setPage(pageCount - 1) }, [page, pageCount])
  const pageItems = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)

  const followers = (users ?? []).filter((u) => u.watchlist_count > 0)

  useEffect(() => {
    if (tab !== 'watchlists' || watchUserId == null) return
    let cancelled = false
    setWatchItems(null)
    api<WatchItem[]>(`/admin/users/${watchUserId}/watchlist`)
      .then((items) => { if (!cancelled) setWatchItems(items) })
      .catch((err) => {
        if (!cancelled) setActionError(err instanceof Error ? err.message : String(err))
      })
    return () => { cancelled = true }
  }, [tab, watchUserId])

  async function addUser(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setBusy(true)
    setActionError(null)
    try {
      await api('/admin/users', {
        method: 'POST',
        body: JSON.stringify({ email: email.trim(), password, role: newRole }),
      })
      setEmail('')
      setPassword('')
      setNewRole('user')
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function patchUser(id: number, fields: { is_active?: boolean; role?: Role }) {
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

  async function sendActivation(id: number) {
    setBusy(true)
    setActionError(null)
    try {
      await api(`/admin/users/${id}/resend-verification`, { method: 'POST' })
      setActivationSent((prev) => new Set(prev).add(id))
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  function openWatchlist(id: number) {
    setWatchUserId(id)
    setTab('watchlists')
  }

  const watchUser = (users ?? []).find((u) => u.id === watchUserId) ?? null

  return (
    <div className="page">
      <h1>Admin</h1>
      <p className="sub">
        Manage accounts: create users, assign roles, suspend access, resend
        activation emails and inspect watchlists.
      </p>

      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'users'}
          className={tab === 'users' ? 'active' : ''}
          onClick={() => setTab('users')}
        >
          Users
        </button>
        <button
          role="tab"
          aria-selected={tab === 'watchlists'}
          className={tab === 'watchlists' ? 'active' : ''}
          onClick={() => setTab('watchlists')}
        >
          Watchlists
        </button>
      </div>

      {(actionError || error) && <p className="error">{actionError ?? error}</p>}

      {tab === 'users' && (
        <>
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
            <label className="field">
              Role
              <select value={newRole} onChange={(e) => setNewRole(e.target.value as Role)}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
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

          <Pagination page={page} pageCount={pageCount} total={filtered.length} pageSize={PAGE_SIZE} onPage={setPage} />
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Watchlist</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {pageItems.map((u) => {
                  const self = me != null && String(u.id) === String(me.id)
                  return (
                    <tr key={u.id}>
                      <td className="mono">{u.email}{self && ' (you)'}</td>
                      <td>
                        <select
                          value={u.role}
                          disabled={busy || self}
                          title={self ? 'You cannot change your own role' : undefined}
                          aria-label={`Role for ${u.email}`}
                          onChange={(e) => patchUser(u.id, { role: e.target.value as Role })}
                        >
                          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                        </select>
                      </td>
                      <td>
                        {u.is_active
                          ? <span className="tag other">active</span>
                          : <span className="tag open">suspended</span>}
                        {!u.is_verified && <span className="mywatch-seen"> unverified</span>}
                      </td>
                      <td className="mono">
                        {u.watchlist_count}{' '}
                        {u.watchlist_count > 0 && (
                          <button className="ghost" onClick={() => openWatchlist(u.id)}>View</button>
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
                        </button>
                        {!u.is_verified && (
                          activationSent.has(u.id) ? (
                            <span className="mywatch-seen"> activation sent</span>
                          ) : (
                            <>
                              {' '}
                              <button className="ghost" disabled={busy} onClick={() => sendActivation(u.id)}>
                                Send activation
                              </button>
                            </>
                          )
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pageCount={pageCount} total={filtered.length} pageSize={PAGE_SIZE} onPage={setPage} />
        </>
      )}

      {tab === 'watchlists' && (
        <>
          <form className="filters" onSubmit={(e) => e.preventDefault()}>
            <label className="field">
              User
              <select
                value={watchUserId ?? ''}
                onChange={(e) => setWatchUserId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">Select a user...</option>
                {followers.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.email} ({u.watchlist_count})
                  </option>
                ))}
              </select>
            </label>
            {watchUser && <span className="filter-count">{watchUser.watchlist_count} ships</span>}
          </form>

          {watchUserId == null ? (
            <p className="empty">Pick a user to see their watchlist. Only users with at least one followed ship are listed.</p>
          ) : watchItems == null ? (
            <p className="empty">Loading watchlist...</p>
          ) : watchItems.length === 0 ? (
            <p className="empty">This watchlist is empty.</p>
          ) : (
            <div className="table-scroll">
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
                  {watchItems.map((v) => (
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
            </div>
          )}
        </>
      )}
    </div>
  )
}
