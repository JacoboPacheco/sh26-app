import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/archivo/wdth.css'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'

// A new deploy replaces the hashed chunk files: a tab opened before it then fails to load a lazy page ("Failed to
// fetch dynamically imported module"). Reload once to pick up the new build; the stamp stops a reload loop.
window.addEventListener('vite:preloadError', (e) => {
  let last = 0
  try {
    last = Number(sessionStorage.getItem('overload.chunkReload')) || 0
  } catch {
    last = 0
  }
  if (Date.now() - last < 30000) return // just reloaded for this: let the error show
  try {
    sessionStorage.setItem('overload.chunkReload', String(Date.now()))
  } catch {
    // storage blocked: reload anyway
  }
  e.preventDefault()
  window.location.reload()
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
