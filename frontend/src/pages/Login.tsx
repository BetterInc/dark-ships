import { FormEvent, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function Login() {
  const { login, resendVerification, googleAuthUrl } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null)
  const [resent, setResent] = useState(false)

  // where to send the user after a successful login (RequireAuth stashes this)
  const from = (location.state as { from?: string } | null)?.from ?? '/'
  const note = (location.state as { note?: string } | null)?.note ?? null

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const fields = new FormData(e.currentTarget)
    const email = String(fields.get('email'))
    setBusy(true)
    setError(null)
    setUnverifiedEmail(null)
    setResent(false)
    try {
      await login(email, String(fields.get('password')))
      navigate(from, { replace: true })
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      if (msg === 'LOGIN_USER_NOT_VERIFIED') {
        setError('Your email address is not verified yet. Check your inbox for the activation link.')
        setUnverifiedEmail(email)
      } else {
        setError(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    if (!unverifiedEmail) return
    try {
      await resendVerification(unverifiedEmail)
      setResent(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function google() {
    setError(null)
    try {
      window.location.href = await googleAuthUrl()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <h1>Sign in</h1>
        <p className="auth-sub">Access your watchlist and tracking tools.</p>

        {note && <p className="notice">{note}</p>}
        {error && <p className="error">{error}</p>}
        {unverifiedEmail && (
          resent ? (
            <p className="notice">Activation email sent to {unverifiedEmail}. Check your inbox.</p>
          ) : (
            <button className="ghost" type="button" onClick={resend}>
              Resend activation email
            </button>
          )
        )}

        <form className="auth-form" onSubmit={submit}>
          <label className="field">
            Email
            <input name="email" type="email" required autoComplete="email" placeholder="you@example.com" />
          </label>
          <label className="field">
            Password
            <input name="password" type="password" required autoComplete="current-password" placeholder="••••••••" />
          </label>
          <button className="primary auth-submit" disabled={busy}>
            {busy ? 'Signing in...' : 'Sign in'}
          </button>
        </form>

        <div className="auth-or"><span>or</span></div>

        <button className="ghost auth-google" onClick={google} type="button">
          Continue with Google
        </button>

        <div className="auth-links">
          <Link to="/forgot-password">Forgot password?</Link>
          <span>New here? <Link to="/register">Create an account</Link></span>
        </div>
      </div>
    </div>
  )
}
