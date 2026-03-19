import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // Base path for production build (served at /admin/)
  // Use VITE_BASE_PATH env var if set, otherwise default to '/' for local dev
  base: process.env.VITE_BASE_PATH || '/',
  server: {
    port: 5173,
    proxy: {
      // Only proxy paths starting with /api/ (not /api-keys, etc.)
      '/api/': {
        target: 'http://localhost:8005',
        changeOrigin: true,
      },
    },
  },
  build: {
    // Output directory for production build
    outDir: 'dist',
    // Generate source maps for debugging
    sourcemap: false,
  },
})
