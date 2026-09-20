import { useFlags, useFlagsmithLoading } from '@flagsmith/flagsmith/react'

// Same Flagsmith-gated-feature pattern as `train-insights` (M8) --
// disabled in production, enabled in development (configured per-
// environment in Flagsmith directly, not in code -- see
// `deploy/.env`/`VITE_FLAGSMITH_ENV_ID` for how prod/dev already point at
// separate Flagsmith environments). Gates both the Metro|V/Line nav tab
// switcher and the `/vline` route itself, so turning the flag off in an
// environment fully hides the feature, not just its nav entry.
export function useVlineFeatureEnabled(): boolean {
  const flags = useFlags(['train-vline'])
  return flags['train-vline']?.enabled ?? false
}

export type VlineRouteGate = 'loading' | 'enabled' | 'disabled'

// Distinguishes "flag not loaded yet" from "confirmed off" -- both look
// disabled to `useVlineFeatureEnabled()` alone, which would otherwise
// redirect away a direct /vline visit before Flagsmith's fetch resolves.
export function useVlineRouteGate(): VlineRouteGate {
  const enabled = useVlineFeatureEnabled()
  const loadingState = useFlagsmithLoading()
  if (loadingState?.isLoading) return 'loading'
  return enabled ? 'enabled' : 'disabled'
}
