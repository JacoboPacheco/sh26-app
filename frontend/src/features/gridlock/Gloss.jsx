import { useId, useLayoutEffect, useRef, useState } from 'react'

// A term explained where it appears: the word (dotted underline) with its one-line definition shown on hover or keyboard
// focus (a tap focuses it on a phone), and read by screen readers as the word's description. `icon`: a small (i) in place
// of a word (the definition is then its label). The definitions the Build together page uses are in glossary.js, so every
// place says the same thing.
//
// The definition stays display:none until it opens (a merely invisible one still widened the phone page by 114 px), and
// when it opens it is nudged sideways to stay inside the panel that scrolls it (or the window), 12 px from either edge.
// Escape closes it.

const EDGE = 12

// the box the tip must stay inside: the nearest ancestor that clips or scrolls sideways (the rail, the phone page), within
// the window; a narrow clipping box (a one-line label cut with an ellipsis) is skipped, the tip can't fit in it anyway
function boundsFor(el) {
  const vw = document.documentElement.clientWidth || window.innerWidth
  let lo = 0
  let hi = vw
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    if (getComputedStyle(p).overflowX === 'visible') continue
    const r = p.getBoundingClientRect()
    if (r.width < 240) continue
    lo = Math.max(lo, r.left)
    hi = Math.min(hi, r.right)
    break
  }
  return [lo + EDGE, hi - EDGE]
}

export default function Gloss({ tip, children, icon = false, label }) {
  const id = useId()
  const [hover, setHover] = useState(false)
  const [focus, setFocus] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const tipRef = useRef(null)
  const open = (hover || focus) && !dismissed

  useLayoutEffect(() => {
    const el = tipRef.current
    if (!open || !el) return
    el.style.setProperty('--gl-tip-dx', '0px')
    const r = el.getBoundingClientRect()
    const [lo, hi] = boundsFor(el)
    let dx = 0
    if (r.right > hi) dx = hi - r.right
    if (r.left + dx < lo) dx = lo - r.left
    if (dx) el.style.setProperty('--gl-tip-dx', `${Math.round(dx)}px`)
  }, [open])

  const reset = () => setDismissed(false)
  return (
    <span
      className={`gl-gloss${icon ? ' gl-gloss--icon' : ''}`}
      tabIndex={0}
      aria-describedby={id}
      aria-label={icon ? label || 'About this' : undefined}
      onPointerEnter={() => setHover(true)}
      onPointerLeave={() => {
        setHover(false)
        reset()
      }}
      onFocus={() => setFocus(true)}
      onBlur={() => {
        setFocus(false)
        reset()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.stopPropagation()
          setDismissed(true)
        }
      }}
    >
      {icon ? (
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6.2" />
          <path d="M8 7.2v4M8 4.9v.1" />
        </svg>
      ) : (
        children
      )}
      <span ref={tipRef} className={`gl-gloss__tip${open ? ' is-open' : ''}`} role="tooltip" id={id}>
        {tip}
      </span>
    </span>
  )
}
