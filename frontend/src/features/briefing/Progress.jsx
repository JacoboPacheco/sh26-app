import { T } from './text'

// One segment per slide, filling with narration time; a click jumps to that slide.
export default function Progress({ slides, idx, progress, lang, onJump, playing = false }) {
  const n = slides.length
  return (
    <ol className="rs-progress" aria-label={lang === 'es' ? 'Diapositivas' : 'Slides'}>
      {slides.map((s, i) => {
        const fill = i < idx ? 1 : i === idx ? progress : 0
        return (
          <li key={s.id}>
            <button
              type="button"
              className={i === idx ? 'rs-progress__seg rs-progress__seg--now' : 'rs-progress__seg'}
              style={{ '--fill': fill }}
              onClick={() => onJump(i)}
              aria-label={`${T[lang].slideOf(i + 1, n)}: ${s.headline?.[lang] || s.headline?.en || s.id}`}
              aria-current={i === idx ? 'step' : undefined}
            >
              {i === idx && playing && <span className="rs-progress__head" aria-hidden="true" />}
            </button>
          </li>
        )
      })}
    </ol>
  )
}
