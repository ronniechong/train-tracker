import { copyFileSync, mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const require = createRequire(import.meta.url)

// maplibre-gl's worker (node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs)
// is a static file, requested at runtime via a `new URL('maplibre-gl-
// worker.mjs', import.meta.url)`-style relative lookup against wherever the
// main bundle was loaded from -- NOT a statically-analyzable import Rollup's
// build can see and bundle/copy automatically. The dev server resolves it
// fine (Vite serves node_modules files on demand), but `vite build` never
// copies it into dist/assets/, so it silently 404s ONLY in production --
// this was never caught until this project's first-ever live deploy (the
// map hangs on "Loading live map..." forever: no console error visible in
// the main-thread console, since load failures inside a dedicated Worker
// surface as an opaque `ErrorEvent` on the Worker object itself, not a
// normal thrown exception -- confirmed via `new Worker(...).onerror` in a
// live debugging session, not assumed).
//
// The worker file ALSO has its own relative import --
// `import ... from "./maplibre-gl-shared.mjs"` (grepped from the actual
// file, not guessed) -- a second static sibling dependency that's
// separately invisible to Rollup for the exact same reason. Copying only
// the worker file (this fix's first attempt) still 404s on ITS import,
// same failure mode one level deeper. Both files must be copied together.
function copyMaplibreWorker(): Plugin {
  let outDir = 'dist'
  return {
    name: 'copy-maplibre-gl-worker',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir
    },
    closeBundle() {
      const assetsDir = join(outDir, 'assets')
      mkdirSync(assetsDir, { recursive: true })
      for (const file of ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']) {
        copyFileSync(require.resolve(`maplibre-gl/dist/${file}`), join(assetsDir, file))
      }
    },
  }
}

export default defineConfig(({ command }) => ({
  plugins: [react(), copyMaplibreWorker()],
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
