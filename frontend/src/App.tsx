import { useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import { usePolling } from './api/client'
import { useAuth } from './auth/AuthContext'
import RequireAuth from './auth/RequireAuth'
import type { Gap, Vessel } from './api/types'
import Admin from './pages/Admin'
import Events from './pages/Events'
import ForgotPassword from './pages/ForgotPassword'
import Blog from './pages/Blog'
import BlogPost from './pages/BlogPost'
import Imagery from './pages/Imagery'
import LiveMap from './pages/LiveMap'
import Login from './pages/Login'
import Register from './pages/Register'
import ResetPassword from './pages/ResetPassword'
import Sources from './pages/Sources'
import Suggestions from './pages/Suggestions'
import VerifyEmail from './pages/VerifyEmail'
import Watchlist from './pages/Watchlist'

function utcClock(): string {
  return new Date().toISOString().slice(11, 19) + ' UTC'
}

export default function App() {
  const { user, logout } = useAuth()
  const [clock, setClock] = useState(utcClock())
  const [menuOpen, setMenuOpen] = useState(false)
  const { data: vessels } = usePolling<Vessel[]>('/vessels', 60_000)
  const { data: openGaps } = usePolling<Gap[]>('/gaps?status=open', 60_000)
  // the header shows YOUR flags, not the engine's: the suggestion queue
  // lives on the Suggestions page only (401s harmlessly while logged out)
  const { data: myList } = usePolling<{ mmsi: number }[]>('/me/watchlist', 60_000)

  useEffect(() => {
    const t = setInterval(() => setClock(utcClock()), 1000)
    return () => clearInterval(t)
  }, [])

  const active = vessels?.filter((v) => v.active).length ?? '-'
  const gaps = openGaps?.length ?? '-'
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
        <div className={`instrument${typeof gaps === 'number' && gaps > 0 ? ' alert' : ''}`}>
          Open gaps<b>{gaps}</b>
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
          <NavLink to="/watchlist" className={({ isActive }) => (isActive ? 'active' : '')}>
            Watchlist
          </NavLink>
          <NavLink to="/suggestions" className={({ isActive }) => (isActive ? 'active' : '')}>
            Suggestions
          </NavLink>
          <NavLink to="/events" className={({ isActive }) => (isActive ? 'active' : '')}>
            Events
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
        <Routes>
          <Route path="/" element={<LiveMap />} />
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
      </main>
    </div>
  )
}
