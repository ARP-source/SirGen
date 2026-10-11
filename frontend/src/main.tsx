import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom'
import 'uplot/dist/uPlot.min.css'
import './styles.css'
import Surgical from './pages/Surgical'
import Dashboard from './pages/Dashboard'
import Control from './pages/Control'
import PostOp from './pages/PostOp'
import AgentsPage from './pages/AgentsPage'

function Home() {
  return (
    <div className="home">
      <h1>Sentinel-OR</h1>
      <p className="secondary">Research prototype. Offline. Not for clinical use.</p>
      <ul>
        <li><Link to="/surgical">/surgical</Link> <span className="muted">screen 1, surgical view</span></li>
        <li><Link to="/dashboard">/dashboard</Link> <span className="muted">screen 2, how it thinks</span></li>
        <li><Link to="/control">/control</Link> <span className="muted">presenter controls</span></li>
        <li><Link to="/post-op">/post-op</Link> <span className="muted">post-op report draft, from the audit log</span></li>
        <li><Link to="/agents">/agents</Link> <span className="muted">the agent swarm, one card per agent</span></li>
      </ul>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/surgical" element={<Surgical />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/control" element={<Control />} />
        <Route path="/post-op" element={<PostOp />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/agent" element={<Navigate to="/agents" replace />} />
        <Route path="/swarm" element={<Navigate to="/agents" replace />} />
        <Route path="*" element={<Home />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
