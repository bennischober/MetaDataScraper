import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  server: {
    // dev: proxy API calls to the resident app (uv run shrinkray app)
    proxy: {
      '/api': 'http://127.0.0.1:8420',
      '/report': 'http://127.0.0.1:8420',
    },
  },
  build: {
    // the resident app serves this directory
    outDir: 'dist',
    emptyOutDir: true,
  },
})
