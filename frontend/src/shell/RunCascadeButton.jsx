import './cue.css'

// The one look for "Run the cascade": a compact button in the map's own chrome (the same surface, border, radius and
// type as the zoom controls and Start over), a small play mark and one line of text; the blue accent shows on hover
// and keyboard focus only. It is used twice: beside the campus dot right after a drop (shell/CascadeCue) and in
// the bottom bar (shell/Timeline).
//
//   state 'idle'    "Run the cascade"
//   state 'running' "Running…" (the request is out, then the replay plays): quiet and disabled
//   state 'again'   "Run it again" (a run is on screen)
//
// `detail` is the cue beside the dot: its sentence and the Enter shortcut live in the tooltip (title) and in
// aria-keyshortcuts, not on the button. The accessible name stays the label; the sentence is its description.
const T = {
  en: { run: 'Run the cascade', again: 'Run it again', running: 'Running…', sub: 'Trips the worst line and watches it spread', key: 'Enter' },
  es: { run: 'Ejecutar la cascada', again: 'Ejecutar de nuevo', running: 'En marcha…', sub: 'Dispara la línea más cargada y mira cómo se propaga', key: 'Intro' },
}

export default function RunCascadeButton({ state = 'idle', onClick, disabled = false, detail = false, lang = 'en', className = '' }) {
  const t = T[lang] || T.en
  const running = state === 'running'
  const label = running ? t.running : state === 'again' ? t.again : t.run
  const hint = detail && !running
  return (
    <button
      type="button"
      className={`run-btn run-btn--${state}${className ? ` ${className}` : ''}`}
      onClick={onClick}
      disabled={disabled || running}
      aria-label={label}
      aria-busy={running || undefined}
      aria-keyshortcuts={hint ? 'Enter' : undefined}
      title={hint ? `${t.sub} (${t.key})` : undefined}
    >
      <svg className="run-btn__mark" viewBox="0 0 9 10" aria-hidden="true" focusable="false">
        <path d="M0 0.5 8.6 5 0 9.5z" fill="currentColor" />
      </svg>
      <span className="run-btn__label">{label}</span>
    </button>
  )
}
