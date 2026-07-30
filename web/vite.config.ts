import { defineConfig } from 'vite'

export default defineConfig({
  // maplibre-gl ships its own web worker bundle; letting Vite's dev-time
  // dependency pre-bundler rewrite it hangs the worker's script request
  // indefinitely (confirmed locally: request sits at "pending" forever,
  // map never issues a single tile fetch). Excluding it here makes Vite
  // serve the package as-is instead.
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
})
