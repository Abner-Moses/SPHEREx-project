import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend the dev server proxies to (override with SPHEREX_API=http://host:port)
const API = process.env.SPHEREX_API || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': API,
      '/health': API,
    },
  },
  build: {
    chunkSizeWarningLimit: 750, // three.js alone is ~700 kB minified; it is its own long-cached chunk below
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/three')) return 'three'
          if (id.includes('node_modules/react')) return 'react'
          return undefined
        },
      },
    },
  },
})
