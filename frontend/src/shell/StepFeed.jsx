import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../geo'
import { useOverload } from '../store'
import { buildSchedule, SNAP_MS, titleCase } from './cascadeSchedule'
import './steps.css'

// The cascade step by step, in the results column (user, Sat 22:32: "the live events being on the right").
// While the replay plays, each step lands here when the replay reaches it: at the counter's first leap for that
// step (a step that hits nobody: the moment its line snaps), with what tripped and the people it hit, and its
// figure grows with each later leap of the same step. One clock: the replay's own schedule (fx.schedule, the
// counter's and the map's), so the row, the number and the blast agree. Paused mid-way: the steps so far.
// After the replay the whole list sits in the column's closed "More" fold (`all`).
// Incident-room look: plain rows, tabular figures, red only for people hit; the row's fade-in is the only motion.

// the least the box may be (the current step, with its reason, and one played row) and the most (15rem)
const BOX_MIN = 80
const BOX_MAX_REM = 15
// sent by features/component3d when it has re-shared the column's lane with the 3D panel
const LANE_EVENT = 'overload:live-lane'

// why each step's element went out (backend powerflow.cascade_case: step.action)
const CAUSE = {
  trip: 'overloaded, its relay tripped it',
  storm: 'knocked out by the storm',
  shed: 'kept in: the operator cut load to relieve it',
  plant: 'power plants went offline',
}

// the lines or transformers a step took out (a shed step: the one it held in)
const idsOf = (st) => (st.tripped?.length ? st.tripped : st.held_line != null ? [st.held_line] : [])

function describe(st, branchById, subName) {
  const ids = idsOf(st)
  const els = ids.map((id) => {
    const b = branchById.get(id)
    if (!b) return { id, name: `Line ${id}`, kind: 'Line', xf: false }
    const nm = (s) => titleCase(subName(s))
    if (b.from_sub === b.to_sub) return { id, name: `${nm(b.from_sub)} transformer`, kind: 'Transformer', xf: true }
    return { id, name: `${nm(b.from_sub)} → ${nm(b.to_sub)}`, kind: b.kv ? `${Math.round(b.kv)} kV line` : 'Line', xf: false }
  })
  const action = st.action || (st.n === 0 ? 'storm' : 'trip')
  const cause = CAUSE[action] || 'tripped'
  const Cause = cause.charAt(0).toUpperCase() + cause.slice(1)
  if (!els.length) return { ids, what: action === 'plant' ? 'Power plants offline' : 'The grid re-balanced', how: Cause }
  // one element: its kind, then why (a transformer's name already says what it is)
  if (els.length === 1) return { ids, what: els[0].name, how: els[0].xf ? Cause : `${els[0].kind} · ${cause}` }
  // several at once (a storm's corridor): how many, then the first of them
  const xf = els.filter((e) => e.xf).length
  const count = (k, one, many) => k && `${fmt(k)} ${k === 1 ? one : many}`
  const kinds = [count(els.length - xf, 'line', 'lines'), count(xf, 'transformer', 'transformers')].filter(Boolean).join(' and ')
  return { ids, what: `${kinds} ${action === 'storm' ? 'knocked out by the storm' : action === 'trip' ? 'tripped, overloaded' : cause}`, how: `${els[0].name} and ${fmt(els.length - 1)} more` }
}

// when each step lands on a replay's clock (ms from fx.startedAt): its first leap that adds people, else its snap
function landings(schedule) {
  const first = new Map()
  schedule.leaps.forEach((l) => {
    if (l.delta > 0 && !first.has(l.step)) first.set(l.step, l.t)
  })
  return new Map(schedule.tiers.map((tier) => [tier.step, first.get(tier.step) ?? tier.t0 + SNAP_MS * schedule.scale]))
}

export default function StepFeed({ all = false }) {
  const { cascade, step, fx, playing, subById, branchById, subName, focus, subPos } = useOverload()
  const headId = useId()
  const boxRef = useRef(null)
  const n = cascade?.steps?.length || 0
  const live = !all && !!(fx && playing)

  // every step, described once per cascade, with the people it hit on the replay's own count
  const base = useMemo(() => {
    if (!n) return []
    const people = new Map(buildSchedule(cascade, subById, branchById, 0).tiers.map((t) => [t.step, t.people]))
    return cascade.steps.map((st, i) => ({ step: i + 1, ...describe(st, branchById, subName), people: people.get(i + 1) || 0 }))
  }, [cascade, n, subById, branchById, subName])

  // while playing: the replay's clock, advanced at each landing and each leap (a render per event, not per frame)
  const marks = useMemo(() => (live ? landings(fx.schedule) : null), [live, fx])
  const [clock, setClock] = useState({ fx: null, t: -1 })
  useEffect(() => {
    if (!live) return undefined
    const times = new Set(marks.values())
    fx.schedule.leaps.forEach((l) => l.delta > 0 && times.add(l.t))
    const now = performance.now() - fx.startedAt
    const timers = [...times].map((t) => setTimeout(() => setClock({ fx, t }), Math.max(0, t - now)))
    return () => timers.forEach(clearTimeout)
  }, [live, fx, marks])

  const rows = useMemo(() => {
    if (all) return base
    if (!live) return base.slice(0, step)
    const t = clock.fx === fx ? clock.t : -1
    const out = base.slice(0, fx.from)
    base.slice(fx.from).forEach((r) => {
      const at = marks.get(r.step)
      if (at == null || at > t) return
      let people = 0
      fx.schedule.leaps.forEach((l) => {
        if (l.step === r.step && l.delta > 0 && l.t <= t) people += l.delta
      })
      out.push({ ...r, people })
    })
    return out
  }, [all, base, live, step, clock, fx, marks])

  // The box is as tall as the column has room for, and only WHOLE rows show: the newest at the bottom, older ones step
  // out at the top, never a row cut in half (user, Sat 23:15: live events "get cropped out"). Room = the column's own max
  // height (features/component3d shares the lane with the 3D panel and trims the column's extras when it is short) less
  // everything else in it. Re-fit when a row lands, on a resize and when the lane is re-shared.
  useLayoutEffect(() => {
    const box = boxRef.current
    if (!box || all) return undefined
    const col = box.closest('.mc-right')
    const fit = () => {
      if (window.innerWidth <= 860 || !col) {
        box.style.height = '' // phones: the column is in the page flow, the stylesheet's height stands
      } else {
        const max = parseFloat(getComputedStyle(col).maxHeight)
        const others = col.scrollHeight - box.offsetHeight
        const chrome = col.offsetHeight - col.clientHeight
        const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
        if (Number.isFinite(max)) {
          const h = Math.max(BOX_MIN, Math.min(rem * BOX_MAX_REM, Math.floor(max - chrome - others)))
          if (box.style.height !== `${h}px`) box.style.height = `${h}px`
        }
      }
      const items = [...box.querySelectorAll('.stp__item')]
      items.forEach((li) => {
        li.hidden = false
      })
      let used = 0
      let first = items.length
      for (let i = items.length - 1; i >= 0; i--) {
        used += items[i].offsetHeight
        if (used > box.clientHeight + 0.5 && first < items.length) break
        first = i
      }
      items.forEach((li, i) => {
        li.hidden = i < first
      })
      box.classList.toggle('stp__box--full', first > 0) // older rows stepped out: the rest sit on the box's floor
      box.scrollTop = box.scrollHeight // (only if even the newest row is taller than the box)
    }
    fit()
    window.addEventListener('resize', fit)
    window.addEventListener(LANE_EVENT, fit)
    return () => {
      window.removeEventListener('resize', fit)
      window.removeEventListener(LANE_EVENT, fit)
    }
  }, [rows, all])

  // pointing at a row lights its line on the map (CascadeFX listens); the light must not outlive the list
  const aimed = useRef(false)
  const aim = (id) => {
    aimed.current = id != null
    window.dispatchEvent(new CustomEvent('overload:aim-line', { detail: { id } }))
  }
  useEffect(
    () => () => {
      if (aimed.current) window.dispatchEvent(new CustomEvent('overload:aim-line', { detail: { id: null } }))
    },
    [],
  )
  const show = (ids) => {
    const pts = []
    ids.forEach((id) => {
      const b = branchById.get(id)
      if (b) pts.push(subPos(b.from_sub), subPos(b.to_sub))
    })
    if (pts.length) focus(pts)
  }

  if (!n) return null
  const now = all ? -1 : rows.length ? rows[rows.length - 1].step : -1
  return (
    <section className={`stp${all ? ' stp--all' : ''}`} aria-labelledby={headId}>
      <div className="stp__head">
        <h2 id={headId} className="panel-h">
          {all ? (
            `Every step (${fmt(n)})`
          ) : (
            <>
              Step by step <span className="stp__of">{rows.length ? `${fmt(now)} of ${fmt(n)}` : `${fmt(n)} steps`}</span>
            </>
          )}
        </h2>
        <span className="stp__count">people hit (estimate)</span>
      </div>
      <div className="stp__box" ref={boxRef}>
        {rows.length ? (
          <ol className="stp__list" aria-live={live ? 'polite' : 'off'} aria-relevant="additions">
            {rows.map((r) => (
              <li key={r.step} className={`stp__item${r.step === now ? ' stp__item--now' : ''}${live && r.step > fx.from ? ' stp__item--in' : ''}`} aria-current={r.step === now ? 'step' : undefined}>
                <button
                  type="button"
                  className="stp__row"
                  onClick={() => show(r.ids)}
                  onMouseEnter={() => r.ids.length && aim(r.ids[0])}
                  onMouseLeave={() => aimed.current && aim(null)}
                  onFocus={() => r.ids.length && aim(r.ids[0])}
                  onBlur={() => aimed.current && aim(null)}
                  aria-label={`Step ${r.step}: ${r.what}, ${r.how}. ${r.people > 0 ? `About ${fmt(r.people)} people hit (estimate).` : 'No one hit.'} Show it on the map.`}
                >
                  <span className="stp__n">{r.step}</span>
                  <span className="stp__what">{r.what}</span>
                  <span className={`stp__people${r.people > 0 ? '' : ' stp__people--none'}`}>{r.people > 0 ? fmt(r.people) : 'none'}</span>
                  <span className="stp__how">{r.how}</span>
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className="stp__wait">The first line is about to trip.</p>
        )}
      </div>
    </section>
  )
}
