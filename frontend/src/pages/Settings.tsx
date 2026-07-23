import { useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'

// Track-window choices offered to signed-in users. The backend caps at 30 days
// (TRACK_WINDOW_MAX_HOURS); keep the largest option in sync with that.
const TRACK_WINDOW_OPTIONS: { label: string; hours: number }[] = [
  { label: '72 hours (default)', hours: 72 },
  { label: '7 days', hours: 24 * 7 },
  { label: '14 days', hours: 24 * 14 },
  { label: '30 days', hours: 24 * 30 },
]

export default function Settings() {
  const { user, refreshUser } = useAuth()
  const [hours, setHours] = useState<number>(user?.track_window_hours ?? 72)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setBusy(true)
    setSaved(false)
    setError(null)
    try {
      await api('/users/me', {
        method: 'PATCH',
        body: JSON.stringify({ track_window_hours: hours }),
      })
      await refreshUser()
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <h1>Settings</h1>
        <p className="auth-sub">Preferences for your account.</p>

        {error && <p className="error">{error}</p>}
        {saved && <p className="notice">Saved.</p>}

        <label className="field">
          Vessel track history
          <select
            value={hours}
            onChange={(e) => { setHours(Number(e.target.value)); setSaved(false) }}
          >
            {TRACK_WINDOW_OPTIONS.map((o) => (
              <option key={o.hours} value={o.hours}>{o.label}</option>
            ))}
          </select>
        </label>
        <p className="auth-sub">
          How far back a ship's track is drawn when you click it on the map.
          Visitors who are not signed in always see the last 72 hours.
        </p>

        <button className="primary auth-submit" disabled={busy} onClick={save}>
          {busy ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}
