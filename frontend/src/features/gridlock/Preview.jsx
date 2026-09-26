// #/preview/gridlock — the Build plans module full-screen, before it has a place in the app.
// Portaled to <body> in a fixed overlay so it covers the Florida map the preview host sits on.
import { createPortal } from 'react-dom'
import BuildPlansPage from './BuildPlansPage'
import './gridlock.css'

export default function Preview() {
  return createPortal(
    <div className="gl-overlay">
      <BuildPlansPage
        extra={
          <a className="gl-link" href="#/">
            Close preview
          </a>
        }
      />
    </div>,
    document.body,
  )
}
