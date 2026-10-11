import { Link, useLocation } from 'react-router-dom'

const LINKS: { to: string; label: string; swarm?: boolean }[] = [
  { to: '/surgical', label: 'Surgical' },
  { to: '/agents', label: 'Swarm agents', swarm: true },
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/control', label: 'Control' },
  { to: '/post-op', label: 'Post-op' },
]

/** Same app, every screen — the 8-card swarm is only at /agents, not on /dashboard. */
export default function AppNav() {
  const { pathname } = useLocation()
  return (
    <nav className="app-nav" aria-label="Screens">
      {LINKS.map(({ to, label, swarm }) => (
        <Link key={to} to={to} className={`app-nav-link${pathname === to ? ' on' : ''}${swarm ? ' swarm' : ''}`}>
          {label}
        </Link>
      ))}
    </nav>
  )
}
