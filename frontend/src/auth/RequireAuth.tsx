import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'

export default function RequireAuth({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return <div className="page"><p className="empty">Checking your session...</p></div>
  }

  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname, note: 'Log in to use your watchlist.' }}
      />
    )
  }

  if (admin && !user.is_superuser) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
