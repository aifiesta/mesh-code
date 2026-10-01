import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Built assets are served by the Python engine itself (StaticFiles), so the
// UI and the WebSocket share one origin. That keeps the engine's Origin
// check simple and avoids file:// CSP problems inside Electron.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  server: { port: 5174 },
})
