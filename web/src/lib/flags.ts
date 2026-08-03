import flagsmith from '@flagsmith/flagsmith'
import { FLAGSMITH_ENV_ID } from '../config'

export { flagsmith }

// undefined (not an object) when unset -- FlagsmithProvider only calls
// flagsmith.init() if `options` is truthy, so this is how we skip
// initialisation entirely in dev / before the env var is configured,
// rather than the provider trying to init with an empty environmentID.
export const flagsmithOptions = FLAGSMITH_ENV_ID
  ? { environmentID: FLAGSMITH_ENV_ID }
  : undefined
