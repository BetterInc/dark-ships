import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api, apiForm, getToken, setToken } from '../api/client'

export interface User {
  id: string
  email: string
  role: 'user' | 'partner' | 'admin'
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
}

interface AuthValue {
  user: User | null
  token: string | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
  forgotPassword: (email: string) => Promise<void>
  resetPassword: (token: string, password: string) => Promise<void>
  verifyEmail: (token: string) => Promise<void>
  resendVerification: (email: string) => Promise<void>
  googleAuthUrl: () => Promise<string>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setTok] = useState<string | null>(getToken())
  const [loading, setLoading] = useState(true)

  // On mount (or when the token changes), hydrate the user from /users/me.
  useEffect(() => {
    let cancelled = false
    async function hydrate() {
      if (!getToken()) {
        setUser(null)
        setLoading(false)
        return
      }
      try {
        const me = await api<User>('/users/me')
        if (!cancelled) setUser(me)
      } catch {
        // bad / expired token: api() already cleared it on 401
        setToken(null)
        if (!cancelled) {
          setTok(null)
          setUser(null)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    hydrate()
    return () => { cancelled = true }
  }, [token])

  async function login(email: string, password: string) {
    const res = await apiForm<{ access_token: string }>('/auth/jwt/login', {
      username: email,
      password,
    })
    setToken(res.access_token)
    const me = await api<User>('/users/me')
    setTok(res.access_token)
    setUser(me)
  }

  async function register(email: string, password: string) {
    await api('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    // no auto-login: the account must verify its email address first
    // (login requires is_verified; the register page points at the inbox)
  }

  function logout() {
    setToken(null)
    setTok(null)
    setUser(null)
  }

  async function forgotPassword(email: string) {
    await api('/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify({ email }),
    })
  }

  async function resetPassword(resetToken: string, password: string) {
    await api('/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify({ token: resetToken, password }),
    })
  }

  async function verifyEmail(verifyToken: string) {
    await api('/auth/verify', {
      method: 'POST',
      body: JSON.stringify({ token: verifyToken }),
    })
  }

  async function resendVerification(email: string) {
    // always 202, whether or not the address exists (no account enumeration)
    await api('/auth/request-verify-token', {
      method: 'POST',
      body: JSON.stringify({ email }),
    })
  }

  async function googleAuthUrl() {
    const res = await api<{ authorization_url: string }>('/auth/google/authorize')
    return res.authorization_url
  }

  return (
    <AuthContext.Provider
      value={{ user, token, loading, login, register, logout, forgotPassword, resetPassword, verifyEmail, resendVerification, googleAuthUrl }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
