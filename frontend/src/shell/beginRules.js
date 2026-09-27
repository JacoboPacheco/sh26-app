// The quiet start gate's rules: when it shows, and how its first click is swallowed.
// shell/BeginGate.jsx draws it; features/flow/Intro.jsx sequences it with the opening (the grid powering on).
//
// It shows on the bare home page only: the address is # or #/ when the page loads (a deep link such as the
// hero's #/?at=…&mw=… or any other page skips it), once per browser tab session, never in an automated
// browser (like the opening, so tests and the deployed smoke run are never blocked; add ?begin to the address
// to see it anyway). Reloading after it was dismissed doesn't bring it back.

const KEY = 'overload:begin-seen'

const bare = (h) => h === '' || h === '#' || h === '#/'
// Read once, when the page loads: the store rewrites the hash later (a dropped case writes #/?at=…), and a
// viewer who came in through the tabs from another page has already started.
const LOADED_ON_HOME = typeof window !== 'undefined' && bare(window.location.hash)

const param = (name) => {
  try {
    return new URLSearchParams(window.location.search).has(name)
  } catch {
    return false
  }
}
const seen = () => {
  try {
    return window.sessionStorage.getItem(KEY) === '1'
  } catch {
    return false // storage blocked or empty: showing it again is harmless
  }
}

/** remember that this tab has begun (a click, tap or key on the gate, or a page that skipped it) */
export function markBegun() {
  try {
    window.sessionStorage.setItem(KEY, '1')
  } catch {
    // private mode or blocked storage: nothing to remember it in
  }
}
if (typeof window !== 'undefined' && !LOADED_ON_HOME) markBegun() // a deep link or another page: this tab is already going

/** true when the gate should be up right now */
export function gateEligible() {
  if (typeof window === 'undefined' || !LOADED_ON_HOME) return false
  if (navigator.webdriver && !param('begin')) return false
  return !seen()
}

/**
 * The click that begins is swallowed, so it never also drops a data center or presses whatever is under the
 * cursor. Called from the capture-phase pointerdown (which already stopped that event); this eats the rest of
 * the same gesture (pointerup, mouseup, click, dblclick), then removes itself. It lives here, not in the
 * gate's effect, because the gate unmounts the moment it begins.
 */
export function swallowGesture(ms = 700) {
  const types = ['pointerup', 'mouseup', 'click', 'dblclick', 'auxclick']
  const eat = (e) => {
    e.stopPropagation()
    e.preventDefault()
  }
  types.forEach((t) => window.addEventListener(t, eat, true))
  window.setTimeout(() => types.forEach((t) => window.removeEventListener(t, eat, true)), ms)
}
