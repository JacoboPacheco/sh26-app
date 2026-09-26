import { lazy, Suspense, useCallback, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

// The top bar's "Watch the story" control. The player (its own chunk) loads only when opened, and renders into
// <body> so no page transform (the map's shake) can move it. #…?story=<episode> opens that episode directly.
const ShowHost = lazy(() => import('./ShowHost'))

const fromHash = () => {
  try {
    return window.location.hash.match(/[?&]story=([\w-]+)/)?.[1] || null
  } catch {
    return null
  }
}

export default function WatchStory({ className = '' }) {
  const [open, setOpen] = useState(() => (fromHash() ? { initial: fromHash() } : null))
  const btn = useRef(null)
  const close = useCallback(() => {
    setOpen(null)
    // drop ?story= so a reload doesn't reopen it
    const h = window.location.hash
    if (/[?&]story=/.test(h)) window.history.replaceState(null, '', h.replace(/([?&])story=[\w-]+&?/, '$1').replace(/[?&]$/, ''))
    requestAnimationFrame(() => btn.current?.focus())
  }, [])
  return (
    <>
      <button type="button" className={`story-btn ${className}`} ref={btn} onClick={() => setOpen({ initial: null })} aria-haspopup="dialog">
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
          <path d="M4.5 2.8v10.4L13 8z" fill="currentColor" />
        </svg>
        Watch the story
      </button>
      {open &&
        createPortal(
          <Suspense fallback={<div className="story-wait" role="status">Opening the story…</div>}>
            <ShowHost initial={open.initial} onClose={close} />
          </Suspense>,
          document.body,
        )}
    </>
  )
}
