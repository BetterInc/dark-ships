import { FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function ForgotPassword() {
  const { forgotPassword } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const fields = new FormData(e.currentTarget)
    setBusy(true)
    setError(null)
    try {
      await forgotPassword(String(fields.get('email')))
      setSent(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <h1>Reset password</h1>
        <p className="auth-sub">
          Enter your email and we will send a link to set a new password.
        </p>

        {error && <p className="error">{error}</p>}

        {sent ? (
          <p className="notice">
            If an account exists for that address, a reset link is on its way.
            Check your inbox.
          </p>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <label className="field">
              Email
              <input name="email" type="email" required autoComplete="email" placeholder="you@example.com" />
            </label>
            <button className="primary auth-submit" disabled={busy}>
              {busy ? 'Sending...' : 'Send reset link'}
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
