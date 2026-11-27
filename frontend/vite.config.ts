import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'
import pkg from './package.json' with { type: 'json' }

// En développement, Vite (port 5173) relaie /api vers le backend FastAPI (port 8000).
// En production, FastAPI sert directement le contenu de dist/.
const backend = process.env.OBOLE_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  define: {
    // Affiché dans les Réglages : permet de vérifier que le téléphone a bien la dernière version
    __APP_VERSION__: JSON.stringify(`${pkg.version} (${new Date().toISOString().slice(0, 16).replace('T', ' ')})`),
  },
  plugins: [
    react(),
    VitePWA({
      registerType: 'prompt',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'Obole',
        short_name: 'Obole',
        description: 'Ma collection de pièces et de billets',
        lang: 'fr',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        background_color: '#121212',
        theme_color: '#121212',
        icons: [
          { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // L'API n'est jamais servie par le service worker : toujours les données à jour du serveur
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // Photos et miniatures : leur contenu ne change jamais (nouvelle URL à chaque ajout)
            urlPattern: ({ url }) => /^\/api\/photos\/\d+(\/thumb)?$/.test(url.pathname),
            handler: 'CacheFirst',
            options: {
              cacheName: 'photos',
              expiration: { maxEntries: 2000, maxAgeSeconds: 365 * 24 * 3600 },
            },
          },
        ],
      },
    }),
  ],
  server: {
    proxy: { '/api': backend },
  },
})
