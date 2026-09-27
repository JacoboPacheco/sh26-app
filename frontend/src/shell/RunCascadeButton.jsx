import { useId } from 'react'
import './cue.css'

// The one look for "Run the cascade": a breaker. A dark opaque body inside a thin red hazard-hatched edge,
// and a small lever that is thrown from off to on when the button is pressed (200 ms, no bounce). It is
// used twice: beside the campus dot right after a drop (shell/CascadeCue) and in the bottom bar (shell/Timeline).
//
//   state 'idle'    lever off, "Run the cascade"
//   state 'running' lever on,  "Running…" (the request is out, then the replay plays)
//   state 'again'   lever off, "Run it again" (a run is on screen)
//
// `detail` adds the sentence under the label and the Enter key hint (the cue beside the dot has room for it).
// The accessible name stays the label; the sentence is its description.
const T = {
  en: { run: 'Run the cascade', again: 'Run it again', running: 'Running…', sub: 'Trip the worst line and watch it spread', key: 'Enter' },
  es: { run: 'Ejecutar la cascada', again: 'Ejecutar de nuevo', running: 'En marcha…', sub: 'Dispara la línea más cargada y mira cómo se propaga', key: 'Intro' },
}

export default function RunCascadeButton({ state = 'idle', onClick, disabled = false, detail = false, lang = 'en', className = '' }) {
  const t = T[lang] || T.en
  const subId = useId()
  const running = state === 'running'
  const label = running ? t.running : state === 'again' ? t.again : t.run
  return (
    <button
      type="button"
      className={`breaker breaker--${state}${detail ? ' breaker--detail' : ''}${className ? ` ${className}` : ''}`}
      onClick={onClick}
      disabled={disabled || running}
      aria-label={label}
      aria-describedby={detail ? subId : undefined}
      aria-busy={running || undefined}
    >
      <span className="breaker__body">
        <span className="breaker__switch" aria-hidden="true">
          <span className="breaker__lever" />
        </span>
        <span className="breaker__label">{label}</span>
        {detail && !running && (
          <kbd className="breaker__key" aria-hidden="true">
            {t.key}
          </kbd>
        )}
        {detail && (
          <span className="breaker__sub" id={subId}>
            {t.sub}
          </span>
        )}
      </span>
    </button>
  )
}
