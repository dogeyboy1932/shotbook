import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Bind to 0.0.0.0, not just loopback, so a forwarded/tunneled 5173
    // (e.g. from a remote GPU VM or container) can actually reach the dev
    // server. Without this Vite listens on 127.0.0.1 only and the forwarder
    // connects to nothing.
    host: true,
    port: 5173,
    // Proxies API calls to the FastAPI backend so the browser only ever
    // needs to reach this dev server's port. Lets frontend + backend run
    // together on a remote box (e.g. the GPU VM) with just one port
    // (5173) tunneled back to a local machine -- no separate API tunnel,
    // and no cross-origin requests for the browser to worry about.
    proxy: {
      '/api': 'http://localhost:8080',
      '/healthz': 'http://localhost:8080',
    },
  },
})
