import { useEffect, useMemo } from 'react'
import { useOverload } from '../../store'
import { weakOf } from './beatMaps'
import { Kicker } from './ShowBits'
import { WEAK } from './showDeck'
import { P } from './showText'
import { useElapsed } from './useShowClock'

// THE WEAK POINT (user, Sat 17:16-17:19: "point toward a problem in the current power grid"). The camera settles on
// the one element that fails first. A gauge pinned beside it shows its loading on today's grid, before any new load;
// power moves along the path any new load at the site takes to it; then the campus's flow arrives (denser, warmer
// particles) and the gauge fills past the rating mark: the new load is the trigger that uses up the last margin.
// Paused or reduced motion: the finished frame (the gauge full, the rating crossed, the particles still).
export default function ShowWeakPoint({ slide, report, lang, animate, stage }) {
  const T = P[lang]
  const { branchById, subPos, grid, result } = useOverload()
  const wk = useMemo(() => weakOf({ branchById, subPos, grid, result }, report, slide), [branchById, subPos, grid, result, report, slide])
  // the new load arrives about when the narration reaches its last sentence ("... used up the last margin")
  const est = (Number(slide.est_s?.[lang]) || 0) * 1000
  const surge = Math.round(Math.min(12000, Math.max(WEAK.surge, est * 0.62)))
  const clock = useElapsed(animate && !!wk, surge + WEAK.fill + 3000)
  const from = Number(wk?.from) || 0
  const to = Number(wk?.to) || 0
  // when the fill crosses the rating (the ring turns from strain to overload)
  const crossAt = surge + (to > from ? (WEAK.fill * Math.max(0, 100 - from)) / (to - from) : 0)
  const over = !animate || clock >= crossAt
  const label = slide.headline?.[lang] || slide.headline?.en || ''
  const title = (label.split(':').slice(1).join(':').trim() || label).replace(/^(the|el|la)\s+/i, '')
  const still = !animate

  useEffect(() => {
    if (!wk || !stage) return
    const flow = wk.path.map((p) => (p.role === 'surge' ? { ...p, tone: 'surge', delay: still ? 0 : surge } : { ...p, tone: 'base' }))
    stage.layer({
      key: 'weak',
      still,
      lines: wk.transformer ? [] : [{ id: wk.id, tone: 'hl' }],
      flow,
      marks: [{ at: wk.at, tone: over ? 'over' : 'strain', r: 15 }],
      gauge: {
        at: wk.at,
        from,
        to,
        ms: still ? 1 : WEAK.fill,
        delay: still ? 0 : surge,
        appear: still ? 0 : WEAK.at,
        title: title.charAt(0).toUpperCase() + title.slice(1),
        sub: T.gaugeSub,
        ratingLabel: T.rating,
      },
    })
  }, [wk, stage, still, over, from, to, title, T, surge])
  useEffect(() => () => stage?.layer(null), [stage])

  const lines = slide.lines?.[lang] || slide.lines?.en || []
  return (
    <>
      <div className="sh-headrow">
        <Kicker tone="amber">{T.weakKicker}</Kicker>
      </div>
      <h2 className="rs-headline sh-weak__h" id={`rs-h-${slide.id}`}>
        {label}
      </h2>
      {lines.length > 0 && (
        <ul className="rs-lines sh-stagger sh-weak__facts">
          {lines.map((line, i) => (
            <li key={i} style={{ '--i': i + 2 }}>
              <span>{line}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="sh-weak__frame">{T.weakFrame}</p>
      <p className={`sh-weak__trigger${over ? ' sh-weak__trigger--on' : ''}`} aria-live="polite">
        {over && to > 0 ? (
          <>
            {T.lastMargin}: <b>{Math.round(to)}%</b>
          </>
        ) : (
          ' '
        )}
      </p>
    </>
  )
}
