import { useEffect, useRef, useState } from 'react'

const TOKEN_KEY = 'ds_token'

// On Cloudflare Pages the frontend is static and the API lives on another
// origin (e.g. https://api.darkships.org), set via VITE_API_BASE_URL at build
// time. Locally this is empty, so requests hit /api and Vite proxies them.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

/** Absolute URL of a raw API resource (images etc.) for use in href/src. */
export function apiUrl(path: string): string {
  return `${API_BASE}/api${path}`
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** Pull a human-friendly message out of a FastAPI-Users error body. */
function errorMessage(body: unknown, resp: Response): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    // fastapi-users returns { detail: { code, reason } } on some failures
    if (detail && typeof detail === 'object') {
      const d = detail as Record<string, unknown>
      if (typeof d.reason === 'string') return d.reason
      if (typeof d.code === 'string') return d.code
    }
    // pydantic validation errors: detail is a list of { msg }
    if (Array.isArray(detail) && detail[0] && typeof detail[0] === 'object') {
      const msg = (detail[0] as Record<string, unknown>).msg
      if (typeof msg === 'string') return msg
    }
  }
  return `${resp.status} ${resp.statusText}`
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...init?.headers,
    },
  })
  if (!resp.ok) {
    if (resp.status === 401) setToken(null)
    const body = await resp.json().catch(() => null)
    throw new Error(errorMessage(body, resp))
  }
  if (resp.status === 204) return undefined as T
  return resp.json()
}

/** POST a form-urlencoded body (the OAuth2 token endpoint needs this, not JSON). */
export async function apiForm<T>(path: string, fields: Record<string, string>): Promise<T> {
  const resp = await fetch(`${API_BASE}/api${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      ...authHeaders(),
    },
    body: new URLSearchParams(fields).toString(),
  })
  if (!resp.ok) {
    if (resp.status === 401) setToken(null)
    const body = await resp.json().catch(() => null)
    throw new Error(errorMessage(body, resp))
  }
  if (resp.status === 204) return undefined as T
  return resp.json()
}

/** Haalt `path` elke `intervalMs` opnieuw op. */
export function usePolling<T>(path: string, intervalMs = 30_000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number>()

  const refresh = async () => {
    try {
      setData(await api<T>(path))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    refresh()
    timer.current = window.setInterval(refresh, intervalMs)
    return () => window.clearInterval(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, intervalMs])

  return { data, error, refresh }
}
