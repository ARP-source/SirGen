import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend (FastAPI) on :7000. /control is both a page (GET, HTML) and the presenter
// REST prefix (POST), so HTML navigations to /control are served by the SPA.
const backend = 'http://localhost:7000'
const spaBypass = (req: { method?: string; headers: Record<string, string | string[] | undefined> }) => {
  const accept = String(req.headers.accept ?? '')
  return req.method === 'GET' && accept.includes('text/html') ? '/index.html' : undefined
}
const proxy = {
  '/ws': { target: 'ws://localhost:7000', ws: true },
  '/video.mjpg': backend,
  '/control': { target: backend, bypass: spaBypass },
  '/scenarios': backend,
  '/patient': backend,
  '/health': backend,
  '/state': backend,
  '/vitals': backend,
  '/audit': backend,
  '/report': backend, // JSON, /report/generate and /report.html; the page is /post-op
}

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: 5173, strictPort: true, proxy },
})
