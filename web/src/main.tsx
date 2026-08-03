import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { FlagsmithProvider } from '@flagsmith/flagsmith/react'
import '@fontsource-variable/inter/wght.css'
import './styles/tokens.css'
import './styles/global.css'
import { App } from './App'
import { flagsmith, flagsmithOptions } from './lib/flags'

const container = document.getElementById('app')
if (!container) throw new Error('#app root element not found')

createRoot(container).render(
  <StrictMode>
    <FlagsmithProvider flagsmith={flagsmith} options={flagsmithOptions}>
      <App />
    </FlagsmithProvider>
  </StrictMode>,
)
