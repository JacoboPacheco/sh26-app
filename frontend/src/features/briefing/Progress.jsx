import { useEffect, useRef } from 'react'
import { T } from './text'
import { slideFrac } from './useNarration'

// One segment per slide; a click jumps to that slide. The current segment fills CONTINUOUSLY from the narration's
// clock (user, Sat 19:09: "make sure progress bars flow smoothly instead of chunking"): every frame writes its fill
// straight to the element (no re-render), from the slide's elapsed time against its expected length, never in per-word
// or per-step jumps; it only moves forward. Paused, it stands where it was. Reduced motion: it steps four times a second.
export default function Progress({ slides, idx, progress, lang, onJump, playing = false, clock = null, reduced = false }) {
  const n = slides.length
  const nowRef = useRef(null)
  useEffect(() => {
    const el = nowRef.current
    if (!el) return undefined
    if (!playing || !clock) {
      el.style.setProperty('--fill', String(progress))
      return undefined
    }
    let raf = 0
    let shown = 0
    let last = -1e9
    let gen = null
    const tick = (now) => {
      const c = clock.current
      const mine = !!c && c.idx === idx
      const target = mine ? slideFrac(c, now) : shown
      if (mine && c.gen !== gen) {
        // a new clock for this slide (it restarted, e.g. in another language): the bar starts over with it, instead of
        // holding its old place until the new clock catches up
        gen = c.gen
        shown = target
        last = -1e9
      }
      if (!reduced || now - last >= 250 || target >= 1) {
        last = now
        shown = Math.max(shown, target)
        el.style.setProperty('--fill', shown.toFixed(4))
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, idx, clock, progress, reduced])

  return (
    <ol className="rs-progress" aria-label={lang === 'es' ? 'Diapositivas' : 'Slides'}>
      {slides.map((s, i) => {
        const now = i === idx
        return (
          <li key={s.id}>
            <button
              type="button"
              ref={now ? nowRef : undefined}
              className={now ? 'rs-progress__seg rs-progress__seg--now' : 'rs-progress__seg'}
              style={now ? undefined : { '--fill': i < idx ? 1 : 0 }}
              onClick={() => onJump(i)}
              aria-label={`${T[lang].slideOf(i + 1, n)}: ${s.headline?.[lang] || s.headline?.en || s.id}`}
              aria-current={now ? 'step' : undefined}
            >
              {now && playing && <span className="rs-progress__head" aria-hidden="true" />}
            </button>
          </li>
        )
      })}
    </ol>
  )
}
