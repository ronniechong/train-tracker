import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // GitHub Pages serves a project site (no custom domain, per CLAUDE.md's
  // decision table) from https://<user>.github.io/train-tracker/, not the
  // origin root -- asset URLs need this prefix or they 404 once deployed.
  // Only applied at build time so `vite`/`vite preview` (dev/local) keep
  // serving from `/`.
  base: command === 'build' ? '/train-tracker/' : '/',
  // maplibre-gl ships its own web worker bundle; letting Vite's dev-time
  // dependency pre-bundler rewrite it hangs the worker's script request
  // indefinitely (confirmed locally: request sits at "pending" forever,
  // map never issues a single tile fetch). Excluding it here makes Vite
  // serve the package as-is instead.
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
}))
