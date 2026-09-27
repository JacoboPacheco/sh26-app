// The one bar across the top of every page (user, Sat 08:13-08:16: "the bar on the top is horribly shit…
// add a nice high quality bar"; top tabs picked Sat 08:14): the name, the state (on the map), the pages,
// and how AI is used. Tabs are plain links; on the map page `onPick` switches between the destruction demo
// and Strengthen without leaving the page.
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import HowAiIsUsed from '../features/ai/HowAiIsUsed'
import { WatchStory } from '../features/show'
import { useOverload } from '../store'
import './topbar.css'

const PAGES = [
  { id: 'demo', label: 'Watch it fail', href: '#/', title: 'Drop a data center on the grid and watch what fails' },
  { id: 'strengthen', label: 'Strengthen the grid', href: '#/strengthen', title: 'Find the weak points and the cheapest upgrades that let more data centers connect' },
  { id: 'agreement', label: 'Build together', href: '#/plans', title: "Where two utilities' planned projects overlap, and how they could build them together" },
  { id: 'proposals', label: 'Proposed data centers', href: '#/vote', title: 'Look up a real proposed data center: what it could do to a grid, what it could cost, what to ask before it is approved' },
  { id: 'views', label: 'Data', href: '#/views', title: 'Where data centers are; population by state' },
]

export default function TopBar({ active, onPick, withState = false }) {
  // on a phone the tabs are a strip that scrolls sideways: bring the page you are on into view
  const tabsRef = useRef(null)
  useEffect(() => {
    const nav = tabsRef.current
    const on = nav?.querySelector('.is-on')
    if (!nav || !on || nav.scrollWidth <= nav.clientWidth) return
    nav.scrollLeft = Math.max(0, on.offsetLeft - (nav.clientWidth - on.offsetWidth) / 2)
  }, [active])
  // At <=860px the bar wraps to two or three rows depending on how much fits (state name length, the tab
  // strip, "Watch the story"); --appbar-h is a fixed fallback that undercounts it, so the wrapped rows paint
  // over the page below (BLOCKER, phone width). Keep --appbar-h equal to the bar's own measured height,
  // always, so every page that reads it (`.mc`, `.below-bar`, …) starts right under it.
  const barRef = useRef(null)
  useLayoutEffect(() => {
    const el = barRef.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const set = () => document.documentElement.style.setProperty('--appbar-h', `${Math.ceil(el.getBoundingClientRect().height)}px`)
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    window.addEventListener('orientationchange', set)
    return () => {
      ro.disconnect()
      window.removeEventListener('orientationchange', set)
    }
  }, [])
  return (
    <header className="appbar" ref={barRef}>
      <a className="appbar__brand" href="#/" onClick={(e) => onPick?.('demo', e)}>
        Overload
      </a>
      {withState && <StatePicker />}
      <nav className="appbar__tabs" aria-label="Pages" ref={tabsRef}>
        {PAGES.map((p) => (
          <a
            key={p.id}
            href={p.href}
            title={p.title}
            className={`appbar__tab${active === p.id ? ' is-on' : ''}`}
            aria-current={active === p.id ? 'page' : undefined}
            onClick={(e) => onPick?.(p.id, e)}
          >
            {p.label}
          </a>
        ))}
      </nav>
      <div className="appbar__end">
        <WatchStory className="appbar__story" />
        <HowAiIsUsed className="appbar__ai" />
      </div>
    </header>
  )
}

// The state switcher: any of the 48 state models, or the whole-U.S. map (click a state there to open it).
// Another state opens empty (the case is cleared, like Start over; nothing is dropped for you) and the map
// flies to all of it; choosing the state already open zooms back out to all of it (store.pickRegion).
// A listbox, not a native <select>: a select fires no change for the option already chosen, so it couldn't
// zoom back out (user, Sat 22:29). Keyboard: Up/Down/Home/End, Enter/Space, Esc, type-ahead.
const US_OPTION = { code: 'US', name: 'Whole U.S. (click a state)' }

function StatePicker() {
  const { region, regions, pickRegion } = useOverload()
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const rootRef = useRef(null)
  const btnRef = useRef(null)
  const listRef = useRef(null)
  const typed = useRef({ text: '', at: -Infinity })
  const id = useId()
  const options = useMemo(() => (regions ? [US_OPTION, ...[...regions].sort((a, b) => a.name.localeCompare(b.name))] : []), [regions])
  const selected = Math.max(0, options.findIndex((r) => r.code === region))

  const openList = (at = selected) => {
    setActive(at)
    setOpen(true)
  }
  const close = (refocus = true) => {
    setOpen(false)
    if (refocus) btnRef.current?.focus()
  }
  const choose = (i) => {
    const r = options[i]
    close()
    if (r) pickRegion(r.code)
  }

  // the list takes the keys while it is open; the option under them stays in view
  useEffect(() => {
    if (open) listRef.current?.focus()
  }, [open])
  useEffect(() => {
    if (open) listRef.current?.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [open, active])
  // a click anywhere else closes it (and leaves the focus where that click put it)
  useEffect(() => {
    if (!open) return undefined
    const away = (e) => {
      if (!rootRef.current?.contains(e.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', away, true)
    return () => document.removeEventListener('pointerdown', away, true)
  }, [open])

  // type-ahead: letters typed within a second spell the start of a state's name (from `base`, the option
  // under the keys; the same letter again steps to the next name starting with it)
  const seek = (ch, base, now) => {
    const t = typed.current
    t.text = now - t.at > 1000 ? ch : t.text + ch
    t.at = now
    const q = t.text.toLowerCase()
    const again = q.length > 1 && [...q].every((c) => c === q[0])
    const from = q.length === 1 || again ? base + 1 : base
    const want = again ? q[0] : q
    for (let k = 0; k < options.length; k++) {
      const i = (from + k) % options.length
      if (options[i].name.toLowerCase().startsWith(want)) return i
    }
    return -1
  }

  const onButtonKey = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      openList()
    } else if (e.key.length === 1 && /\S/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const i = seek(e.key, selected, e.timeStamp)
      if (i >= 0) {
        e.preventDefault()
        openList(i)
      }
    }
  }
  const onListKey = (e) => {
    const last = options.length - 1
    const move = { ArrowDown: Math.min(last, active + 1), ArrowUp: Math.max(0, active - 1), Home: 0, End: last, PageDown: Math.min(last, active + 10), PageUp: Math.max(0, active - 10) }
    if (e.key in move) {
      e.preventDefault()
      setActive(move[e.key])
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (e.key === ' ' && e.timeStamp - typed.current.at < 1000) {
        const i = seek(' ', active, e.timeStamp) // a space inside a typed name ("New Y…")
        if (i >= 0) setActive(i)
        return
      }
      choose(active)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      close()
    } else if (e.key === 'Tab') {
      close() // back on the button first, so Tab carries on from the picker to what comes after it
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const i = seek(e.key, active, e.timeStamp)
      if (i >= 0) setActive(i)
    }
  }

  if (!regions) return null
  const labelId = `${id}-label`
  const btnId = `${id}-btn`
  const optId = (i) => `${id}-opt-${i}`
  return (
    <div className="appbar__state" ref={rootRef}>
      <span className="appbar__sr" id={labelId}>
        State
      </span>
      <button
        type="button"
        id={btnId}
        ref={btnRef}
        className="appbar__state-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-labelledby={`${labelId} ${btnId}`}
        title="Choose a state, or the whole U.S. Choose the one already open to zoom back out to all of it."
        onClick={() => (open ? close() : openList())}
        onKeyDown={onButtonKey}
      >
        <span className="appbar__state-name">{options[selected]?.name}</span>
        <svg className="appbar__state-caret" viewBox="0 0 10 6" width="10" height="6" aria-hidden="true">
          <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <ul
          id={`${id}-list`}
          ref={listRef}
          className="appbar__state-list"
          role="listbox"
          tabIndex={-1}
          aria-labelledby={labelId}
          aria-activedescendant={optId(active)}
          onKeyDown={onListKey}
          onBlur={(e) => !rootRef.current?.contains(e.relatedTarget) && setOpen(false)}
        >
          {options.map((r, i) => (
            <li
              key={r.code}
              id={optId(i)}
              data-i={i}
              role="option"
              aria-selected={i === selected}
              className={`appbar__state-opt${i === active ? ' is-active' : ''}${i === selected ? ' is-selected' : ''}${i === 0 ? ' appbar__state-opt--us' : ''}`}
              onPointerMove={() => i !== active && setActive(i)}
              onClick={() => choose(i)}
            >
              {r.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
