import { Suspense, lazy, useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import { usePolling } from './api/client'
import { useAuth } from './auth/AuthContext'
import RequireAuth from './auth/RequireAuth'

// Route components are lazy-loaded so each page ships as its own chunk. This
// keeps MapLibre (imported only by LiveMap, ~800 kB) out of the shared bundle,
// so Sources/Blog/auth pages load a small chunk instead of the whole app.
const Admin = lazy(() => import('./pages/Admin'))
const Events = lazy(() => import('./pages/Events'))
const ForgotPassword = lazy(() => import('./pages/ForgotPassword'))
const Blog = lazy(() => import('./pages/Blog'))
const BlogPost = lazy(() => import('./pages/BlogPost'))
const Imagery = lazy(() => import('./pages/Imagery'))
const LiveMap = lazy(() => import('./pages/LiveMap'))
const Login = lazy(() => import('./pages/Login'))
const Monitor = lazy(() => import('./pages/Monitor'))
const Register = lazy(() => import('./pages/Register'))
const ResetPassword = lazy(() => import('./pages/ResetPassword'))
const ShipDetails = lazy(() => import('./pages/ShipDetails'))
const Sources = lazy(() => import('./pages/Sources'))
const Suggestions = lazy(() => import('./pages/Suggestions'))
const VerifyEmail = lazy(() => import('./pages/VerifyEmail'))
const Watchlist = lazy(() => import('./pages/Watchlist'))

function utcClock(): string {
  return new Date().toISOString().slice(11, 19) + ' UTC'
}

export default function App() {
  const { user, logout } = useAuth()
  const [clock, setClock] = useState(utcClock())
  const [menuOpen, setMenuOpen] = useState(false)
  const { data: watchCount } = usePolling<{ active: number }>('/vessels/count', 60_000)
  // the header shows YOUR flags, not the engine's: the suggestion queue
  // lives on the Suggestions page only (401s harmlessly while logged out)
  const { data: myList } = usePolling<{ mmsi: number }[]>('/me/watchlist', 60_000)

  useEffect(() => {
    const t = setInterval(() => setClock(utcClock()), 1000)
    return () => clearInterval(t)
  }, [])

  const active = watchCount?.active ?? '-'
  const following = myList?.length ?? 0

  return (
    <div className="app">
      <header className="statusbar">
        <div className="brand">
          Dark Ships <span>/ phase 1</span>
        </div>
        <div className="instrument">
          <span className="live-dot" aria-hidden />
          <b>{clock}</b>
        </div>
        <div className="instrument">
          Watchlist<b>{active}</b>
        </div>
        {user && (
          <div className="instrument">
            Following<b>{following}</b>
          </div>
        )}
        <button
          className="nav-burger"
          aria-label="Menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
        >
          {/* inline SVG, not a font glyph: mobile mono fonts lack U+2630 */}
          <svg width="18" height="14" viewBox="0 0 18 14" aria-hidden="true">
            <path d="M1 1h16M1 7h16M1 13h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
        <nav className={menuOpen ? 'open' : ''} onClick={() => setMenuOpen(false)}>
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>
            Map
          </NavLink>
          <NavLink to="/monitor" className={({ isActive }) => (isActive ? 'active' : '')}>
            Monitor
          </NavLink>
          <NavLink to="/imagery" className={({ isActive }) => (isActive ? 'active' : '')}>
            Imagery
          </NavLink>
          <NavLink to="/sources" className={({ isActive }) => (isActive ? 'active' : '')}>
            Sources
          </NavLink>
          <NavLink to="/blog" className={({ isActive }) => (isActive ? 'active' : '')}>
            Blog
          </NavLink>
          {user?.is_superuser && (
            <NavLink to="/admin" className={({ isActive }) => (isActive ? 'active' : '')}>
              Admin
            </NavLink>
          )}
          <span className="nav-sep" aria-hidden />
          {user ? (
            <span className="nav-account">
              <span className="nav-email" title={user.email}>{user.email}</span>
              <button className="ghost nav-logout" onClick={logout}>Logout</button>
            </span>
          ) : (
            <>
              <NavLink to="/login" className={({ isActive }) => (isActive ? 'active' : '')}>
                Login
              </NavLink>
              <Link to="/register" className="nav-register">Register</Link>
            </>
          )}
        </nav>
      </header>
      <main>
        <Suspense fallback={<div className="route-loading">Loading…</div>}>
        <Routes>
          <Route path="/" element={<LiveMap />} />
          {/* shareable deep link to a vessel; same map, focused on the ship */}
          <Route path="/ship/:mmsi" element={<LiveMap />} />
          {/* full dossier: identity + evidence trail, no map */}
          <Route path="/ship/:mmsi/details" element={<ShipDetails />} />
          <Route path="/monitor" element={<RequireAuth><Monitor /></RequireAuth>} />
          {/* standalone routes kept so existing in-app links keep working */}
          <Route
            path="/watchlist"
            element={<RequireAuth><Watchlist /></RequireAuth>}
          />
          <Route path="/suggestions" element={<RequireAuth><Suggestions /></RequireAuth>} />
          <Route path="/events" element={<RequireAuth><Events /></RequireAuth>} />
          <Route path="/imagery" element={<RequireAuth><Imagery /></RequireAuth>} />
          <Route path="/admin" element={<RequireAuth admin><Admin /></RequireAuth>} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/blog" element={<Blog />} />
          <Route path="/blog/:slug" element={<BlogPost />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/verify" element={<VerifyEmail />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  )
}
