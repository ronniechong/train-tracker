const apiBaseUrl = import.meta.env.VITE_API_BASE_URL

if (!apiBaseUrl) {
  throw new Error(
    'VITE_API_BASE_URL is not set. Copy web/.env.example to web/.env.local and fill in the deployed backend URL.',
  )
}

export const API_BASE_URL = apiBaseUrl
