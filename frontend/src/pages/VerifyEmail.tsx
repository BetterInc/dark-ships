import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function VerifyEmail() {
  const { verifyEmail, resendVerification } = useAuth()
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [state, setState] = useState<'working' | 'done' | 'failed'>('working')
  const [error, setError] = useState<string | null>(null)
  const [resendEmail, setResendEmail] = useState('')
  const [resent, setResent] = useState(false)
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current) return // StrictMode double-mount: a token verifies once
    ran.current = true
    if (!token) {
      setState('failed')
      setError('This activation link is missing its token.')
      return
    }
    verifyEmail(token)
      .then(() => setState('done'))
      .catch((err) => {
        const msg = err instanceof Error ? err.message : String(err)
        if (msg === 'VERIFY_USER_ALREADY_VERIFIED') {
          setState('done') // clicking the link twice is fine
        } else {
          setState('failed')
          setError('This activation link is invalid or has expired.')
        }
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  async function resend() {
    try {
      await resendVerification(resendEmail.trim())
      setResent(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <h1>Activate account</h1>

        {state === 'working' && <p className="notice">Verifying your email address...</p>}

        {state === 'done' && (
          <>
            <p className="notice">
              Your email address is verified. You can now sign in.
            </p>
            <div className="auth-links">
              <Link to="/login">Go to sign in</Link>
            </div>
          </>
        )}

        {state === 'failed' && (
          <>
            {error && <p className="error">{error}</p>}
            {resent ? (
              <p className="notice">
                If an account exists for that address, a new activation email is
                on its way. Check your inbox.
              </p>
            ) : (
              <form
                className="auth-form"
                onSubmit={(e) => { e.preventDefault(); resend() }}
              >
                <label className="field">
                  Email
                  <input
                    type="email"
                    required
                    autoComplete="email"
                    placeholder="you@example.com"
                    value={resendEmail}
                    onChange={(e) => setResendEmail(e.target.value)}
                  />
                </label>
                <button className="primary auth-submit">Send a new activation email</button>
              </form>
            )}
            <div className="auth-links">
              <Link to="/login">Back to sign in</Link>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
