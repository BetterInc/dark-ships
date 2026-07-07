import { FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function Register() {
  const { register, googleAuthUrl } = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
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
      await register(String(fields.get('email')), password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
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
        <h1>Create account</h1>
        <p className="auth-sub">Build a watchlist and track vessels of interest.</p>

        {error && <p className="error">{error}</p>}

        <form className="auth-form" onSubmit={submit}>
          <label className="field">
            Email
            <input name="email" type="email" required autoComplete="email" placeholder="you@example.com" />
          </label>
          <label className="field">
            Password
            <input name="password" type="password" required autoComplete="new-password" minLength={8} placeholder="At least 8 characters" />
          </label>
          <label className="field">
            Confirm password
            <input name="confirm" type="password" required autoComplete="new-password" minLength={8} placeholder="Repeat password" />
          </label>
          <button className="primary auth-submit" disabled={busy}>
            {busy ? 'Creating...' : 'Create account'}
          </button>
        </form>

        <div className="auth-or"><span>or</span></div>

        <button className="ghost auth-google" onClick={google} type="button">
          Continue with Google
        </button>

        <div className="auth-links">
          <span>Already have an account? <Link to="/login">Sign in</Link></span>
        </div>
      </div>
    </div>
  )
}
