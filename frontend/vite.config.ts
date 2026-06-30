import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Load env from the repo root so the WHOLE project is configured by one
  // top-level .env (VITE_* vars are read from ../.env). Only VITE_-prefixed
  // vars are exposed to the bundle, so BVG_/secret entries stay server-side.
  envDir: '..',
  server: {
    // Bind to 0.0.0.0, not just loopback, so a forwarded/tunneled 5173
    // (e.g. from a remote GPU VM or container) can actually reach the dev
    // server. Without this Vite listens on 127.0.0.1 only and the forwarder
    // connects to nothing.
    host: true,
    port: 5173,
    // Accept any Host header. Vite otherwise rejects requests coming through a
    // forwarded/tunneled domain ("Blocked request. This host is not allowed."),
    // which shows up as a blank page in the browser even though the server is
    // healthy. Fine for a dev box; do NOT use `true` on a public production host.
    allowedHosts: true,
    // Proxies API calls to the FastAPI backend so the browser only ever
    // needs to reach this dev server's port. Lets frontend + backend run
    // together on a remote box (e.g. the GPU VM) with just one port
    // (5173) tunneled back to a local machine -- no separate API tunnel,
    // and no cross-origin requests for the browser to worry about.
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        // Long-running MJPEG streams must not time out or buffer.
        timeout: 0,
        proxyTimeout: 0,
      },
      '/healthz': 'http://localhost:8080',
    },
  },
})
