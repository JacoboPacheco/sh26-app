// The AI boom panel's way into the time-lapse: one button; the time-lapse opens full width over the app (a portal on
// <body>, under the top bar) and closes back to the panel. Loaded lazily, so the panel stays light.
import { lazy, Suspense, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useOverload } from '../../store'
import { Button, Loading } from '../../ui'
import './timelapse.css'

const Timelapse = lazy(() => import('./Timelapse'))

export default function TimelapseEntry() {
  const { region, grid } = useOverload()
  const [open, setOpen] = useState(false)
  const btn = useRef(null)
  const national = region === 'US'
  function close() {
    setOpen(false)
    requestAnimationFrame(() => btn.current?.focus())
  }
  return (
    <div className="tl-entry">
      <Button variant="secondary" ref={btn} onClick={() => setOpen(true)} disabled={national || !grid}>
        The AI boom, year by year
      </Button>
      <p className="tl-entry__hint">{national ? 'Pick a state to watch its reported campuses arrive, 2026 to 2035.' : 'Reported campuses arrive 2026 to 2035; watch the grid strain grow.'}</p>
      {open &&
        createPortal(
          <Suspense
            fallback={
              <div className="tl tl--loading">
                <Loading label="Opening the time-lapse…" />
              </div>
            }
          >
            <Timelapse onClose={close} />
          </Suspense>,
          document.body,
        )}
    </div>
  )
}
