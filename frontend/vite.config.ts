import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: false,
  },
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // 'prompt', not 'autoUpdate': confirmed directly (built dist/sw.js
      // under the previous 'autoUpdate' setting) that this generates a
      // service worker calling self.skipWaiting() unconditionally at
      // top level, immediately taking control of every open tab the
      // instant a new version is fetched -- with no client-side listener
      // even registered (registerRegister was left at its default
      // auto-injected bare-registration script, which never calls
      // window.location.reload() either), meaning already-open tabs could
      // silently end up with new-service-worker-controlled network
      // requests running against still-old, already-loaded JS, a real
      // stale-version mismatch risk, not just a missed UX nicety.
      // 'prompt' makes the generated service worker wait for an explicit
      // skip-waiting message instead -- paired with injectRegister:false
      // and hooks/usePwaUpdate.ts's useRegisterSW() below, which is what
      // actually sends that message once the user chooses to update.
      registerType: 'prompt',
      // The default auto-injected registerSW.js is a bare
      // `navigator.serviceWorker.register(...)` call with no update
      // handling at all -- disabled in favor of registering explicitly via
      // virtual:pwa-register/react (hooks/usePwaUpdate.ts), the only way
      // to get an onNeedRefresh callback this app can show real UI for.
      injectRegister: false,
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'AI Try-On',
        short_name: 'Try It On',
        description: 'See how clothes look on you before you buy them.',
        start_url: '/',
        display: 'standalone',
        background_color: '#f8fafc',
        theme_color: '#4f46e5',
        icons: [
          { src: 'icons/pwa-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: 'icons/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: 'icons/pwa-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,ico,webmanifest}'],
        // Match by path, not a hardcoded origin — the backend can be same-
        // or cross-origin depending on VITE_API_BASE_URL (see .env.example)
        // — and NEVER cache it: job status, results, and auth must always
        // hit the network. A stale cached AI result or job status would be
        // actively misleading, not a helpful offline convenience.
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith('/api/'),
            handler: 'NetworkOnly',
          },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
  },
})
