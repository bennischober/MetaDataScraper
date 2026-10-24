import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vitest/config'

// UI unit tests (issue #102): pure-function coverage for the api contract
// helpers, a page render pattern with a mocked api, and the SSE progress
// store with a mocked EventSource. No server required; happy-dom provides
// the DOM (localStorage included) the api helpers touch.
export default defineConfig({
  plugins: [svelte()],
  resolve: {
    // component tests render on the client; without the browser condition
    // vitest resolves svelte's server export and mount() refuses to run
    conditions: ['browser'],
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.test.ts'],
    // the suite is a gate job: fail fast, no watch, no coverage overhead
    watch: false,
  },
})
