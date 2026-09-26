// The Strengthen page's signature: the capacity meter. One cell per campus of the chosen size, in the order the
// engine connects them (all at once, each where it fits with every one before it), up to where the search ends.
//   today's campuses (fit with no upgrade) ... solid light ink
//   bought by the budget ...................... solid green (lights as its step plays in the build-up)
//   possible with more money .................. a thin green outline, empty
//   past the power plants' line ............... hatched: those also need new generation (never hidden)
// Brackets under the cells group them ("2 today", "+5 with $37M", "10 more for another $241M", the same words as
// the plan's budget line); a dashed mark after the last cell says why the search ends. The plants line sits between
// cells where the plants' 15 % reserve runs out; its flag points back at the campuses they cover and keys the hatch.
// Cells are sized to the width (at least 12 px) and wrap to a second row on a phone. Screen readers get one
// summary sentence (`summary`); the plan list beside the map gives keyboard access to every campus.
import { useLayoutEffect, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { shortMoney } from './budget'
import { aOrdinal, stopText } from './capacity'

const MIN = 12
const STAGGER_MS = 45

export default function CapacityMeter({ m, plantsN, reservePct, target, shown, playing, selectedN, onPick, summary }) {
  const ref = useRef(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const measure = () => setW(el.clientWidth)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // when the budget rises, the newly bought cells fill left to right (a short stagger); never when it falls
  const [seen, setSeen] = useState({ target, from: target })
  if (seen.target !== target) setSeen({ target, from: seen.target })
  const from = seen.from

  const steps = m.steps
  const N = steps.length
  const today = m.today
  const wide = w >= 720
  const G = wide ? 4 : 3
  const endW = wide ? 210 : 0
  const cellH = wide ? 34 : 26
  const avail = Math.max(0, w - endW)
  const perRowMax = Math.max(1, Math.floor((avail + G) / (MIN + G)))
  const rows = Math.max(1, Math.ceil(N / perRowMax))
  const perRow = Math.ceil(N / rows)
  const cellW = Math.max(MIN, Math.min(wide ? 112 : 64, Math.floor((avail - (perRow - 1) * G) / Math.max(perRow, 1))))
  const x = (i) => i * (cellW + G) // i: index within its row
  const rowW = (k) => k * (cellW + G) - G

  const total = steps.at(-1)?.cum_cost.high || 0
  const bought = target > 0 ? steps[target - 1].cum_cost.high : 0
  const groups = [
    today > 0 && { from: 1, to: today, kind: 'today', label: `${fmt(today)} today`, short: fmt(today) },
    target > today && { from: today + 1, to: target, kind: 'bought', label: `+${fmt(target - today)} with ${shortMoney(bought)}`, short: `+${fmt(target - today)}` },
    N > target && {
      from: target + 1,
      to: N,
      kind: 'more',
      label: bought > 0 ? `${fmt(N - target)} more for another ${shortMoney(total - bought)}` : `${fmt(N - target)} more with ${shortMoney(total)}`,
      short: `+${fmt(N - target)}`,
    },
  ].filter(Boolean)
  const marker = plantsN != null && plantsN < N ? plantsN : null // index of the first cell past the plants' line

  const stateOf = (n) => (n <= today ? 'today' : n <= target ? 'bought' : 'more')
  const stop = stopText(m.stop)
  const endLine = `${stop.charAt(0).toUpperCase()}${stop.slice(1)}`
  const endSub = m.stop === 'plants' || m.stop === 'no_fix' ? `for ${aOrdinal(N + 1)} campus` : ''

  return (
    <div className="cm" ref={ref}>
      <p className="st-sr">{summary}</p>
      {w > 0 && (
        <div className="cm-rows" aria-hidden="true">
          {Array.from({ length: rows }, (_, r) => {
            const first = r * perRow + 1
            const last = Math.min(N, first + perRow - 1)
            const k = last - first + 1
            const hasMarker = marker != null && Math.floor(marker / perRow) === r
            const mi = hasMarker ? marker % perRow : 0
            const mx = hasMarker ? (mi === 0 ? -G / 2 - 1 : x(mi) - G / 2) : 0
            // the flag: the longest wording that fits to the right of the line (else to its left)
            const flag = hasMarker ? plantsFlag(marker, reservePct, Math.max(rowW(k) - mx, mx)) : null
            const flagRight = hasMarker && rowW(k) - mx < flag.w // no room to its right: the text reads leftward
            const isLast = r === rows - 1
            return (
              <div key={r} className={`cm-row${hasMarker ? ' cm-row--flag' : ''}`} style={{ '--cm-h': `${cellH}px` }}>
                {hasMarker && (
                  <div className={`cm-plants${flagRight ? ' cm-plants--left' : ''}`} style={{ left: `${mx}px` }}>
                    <span className="cm-plants__text">
                      {flagRight ? flag.text.replace(/^← /, '') : flag.text}
                      {flag.hatch && (
                        <span className="cm-plants__key">
                          <span className="cm-plants__swatch" />
                          {flag.hatch}
                        </span>
                      )}
                    </span>
                  </div>
                )}
                <div className="cm-cells" style={{ gap: `${G}px` }}>
                  {steps.slice(first - 1, last).map((st) => {
                    const n = st.n
                    const s = stateOf(n)
                    const lit = s === 'today' || (s === 'bought' && n <= shown)
                    const delay = !playing && target > from && n > from && n <= target ? Math.min(n - from - 1, 14) * STAGGER_MS : 0
                    const cls = [
                      'cm-cell',
                      `cm-cell--${s}`,
                      s === 'bought' && !lit && 'is-unlit',
                      marker != null && n > marker && 'cm-cell--gen',
                      selectedN === n && 'is-sel',
                      playing && n === shown && 'is-now',
                    ]
                      .filter(Boolean)
                      .join(' ')
                    return (
                      <div
                        key={n}
                        className={cls}
                        style={{ width: `${cellW}px`, '--cm-d': `${delay}ms` }}
                        title={`Campus ${n}: ${st.site.area}${st.free ? (n <= today ? ', fits today' : ', fits with the upgrades before it') : `, +${shortMoney(st.cost.high)} of upgrades`}`}
                        onClick={() => onPick(n)}
                      >
                        {cellW >= 18 && <span className="cm-cell__n">{n}</span>}
                      </div>
                    )
                  })}
                  {isLast && wide && (
                    <div className="cm-end" style={{ marginLeft: `${Math.max(10, 14 - G)}px` }}>
                      <span className="cm-end__line">{endLine}</span>
                      {endSub && <span className="cm-end__sub">{endSub}</span>}
                    </div>
                  )}
                </div>
                <div className="cm-brackets" style={{ width: `${rowW(k)}px` }}>
                  {groups.map((g) => {
                    const a = Math.max(g.from, first)
                    const b = Math.min(g.to, last)
                    if (a > b) return null
                    const left = x(a - first)
                    const width = x(b - first) + cellW - left
                    const labelled = a === g.from
                    const fits = g.label.length * 6.4 <= width + (g === groups.at(-1) ? endW + 40 : 6)
                    return (
                      <div key={g.kind} className={`cm-br cm-br--${g.kind}`} style={{ left: `${left}px`, width: `${width}px` }}>
                        {labelled && <span className="cm-br__label">{fits ? g.label : g.short}</span>}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
          {!wide && (
            <p className="cm-end cm-end--below">
              <span className="cm-end__line">Then {stop}</span>
              {endSub && <span className="cm-end__sub"> {endSub}</span>}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// The plants line's flag, longest first: what the plants cover (pointing back at those cells) and a key for the
// hatch on the cells past it. `room` is the widest side of the line in px; the text is about 6.3 px a character.
function plantsFlag(marker, reservePct, room) {
  const reserve = `${fmt(reservePct)} % reserve`
  const opts =
    marker === 0
      ? [
          [`Power plants: no room with a ${reserve}`, 'all need new generation'],
          ['No room at the power plants', 'new generation'],
          ['No room at the plants', null],
        ]
      : [
          [`← Power plants cover ${fmt(marker)} with a ${reserve}`, 'these also need new generation'],
          [`← Plants cover ${fmt(marker)}`, 'need new generation'],
          [`← Plants cover ${fmt(marker)}`, 'new generation'],
          [`← Plants cover ${fmt(marker)}`, null],
        ]
  const sized = opts.map(([text, hatch]) => ({ text, hatch, w: (text.length + (hatch ? hatch.length : 0)) * 6.3 + (hatch ? 28 : 0) + 12 }))
  return sized.find((o) => o.w <= room) || sized.at(-1)
}
