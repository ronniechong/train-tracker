const apiBaseUrl = import.meta.env.VITE_API_BASE_URL

if (!apiBaseUrl) {
  throw new Error(
    'VITE_API_BASE_URL is not set. Copy web/.env.example to web/.env.local and fill in the deployed backend URL.',
  )
}

export const API_BASE_URL = apiBaseUrl

// Optional, unlike API_BASE_URL above: an unset flag env just means every
// feature flag falls back to its default (see flags.ts), not a broken app.
export const FLAGSMITH_ENV_ID = import.meta.env.VITE_FLAGSMITH_ENV_ID || null
