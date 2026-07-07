import { FormEvent, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function ResetPassword() {
  const { resetPassword } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const fields = new FormData(e.currentTarget)
    const password = String(fields.get('password'))
    const confirm = String(fields.get('confirm'))
    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await resetPassword(token, password)
      setDone(true)
      setTimeout(() => navigate('/login', { replace: true }), 1800)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <h1>Set new password</h1>

        {!token && (
          <p className="error">
            This reset link is missing its token. Request a new link from the
            forgot-password page.
          </p>
        )}
        {error && <p className="error">{error}</p>}

        {done ? (
          <p className="notice">
            Password updated. Redirecting you to sign in...
          </p>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <label className="field">
              New password
              <input name="password" type="password" required autoComplete="new-password" minLength={8} placeholder="At least 8 characters" />
            </label>
            <label className="field">
              Confirm password
              <input name="confirm" type="password" required autoComplete="new-password" minLength={8} placeholder="Repeat password" />
            </label>
            <button className="primary auth-submit" disabled={busy || !token}>
              {busy ? 'Saving...' : 'Update password'}
            </button>
          </form>
        )}

        <div className="auth-links">
          <Link to="/login">Back to sign in</Link>
        </div>
      </div>
    </div>
  )
}
