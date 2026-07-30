import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/inter/wght.css'
import './styles/tokens.css'
import './styles/global.css'
import { App } from './App'

const container = document.getElementById('app')
if (!container) throw new Error('#app root element not found')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
