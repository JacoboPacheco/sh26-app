import { fmt } from '../geo'
import { useOverload } from '../store'
import { Button, ErrorBanner } from '../ui'

// The bottom bar: the one place the cascade is started, played and scrubbed, in every mode.
export default function Timeline() {
  const o = useOverload()
  const { cascade, cascading, cascadeError, step, setStep, playing, setPlaying, startCascade, result, subName, branchById } = o
  const ready = !!result // a solved case: a data center, a hurricane track, a heat level…
  const n = cascade?.steps.length || 0
  const cur = cascade && step > 0 ? cascade.steps[step - 1] : null

  function describe(st) {
    if (!st) return 'Before anything trips'
    const names = st.tripped.slice(0, 2).map((id) => {
      const b = branchById.get(id)
      if (!b) return `line ${id}`
      return b.from_sub === b.to_sub ? `${subName(b.from_sub)} transformer` : `${subName(b.from_sub)} → ${subName(b.to_sub)}`
    })
    const more = st.tripped.length > 2 ? ` and ${st.tripped.length - 2} more` : ''
    return `${st.n === 0 ? 'Knocked out' : 'Tripped'}: ${names.join(', ')}${more}`
  }

  return (
    <div className="timeline">
      <div className="timeline__cta">
        <Button onClick={() => startCascade()} busy={cascading} disabled={!ready}>
          {cascading ? 'Running…' : cascade ? 'Run it again' : 'Run the cascade'}
        </Button>
        {cascade && n > 0 && (
          <Button
            variant="secondary"
            onClick={() => {
              if (step >= n) setStep(0)
              setPlaying(!playing || step >= n)
            }}
            aria-label={playing ? 'Pause the cascade' : 'Play the cascade'}
          >
            {playing ? 'Pause' : step >= n ? 'Replay' : 'Play'}
          </Button>
        )}
      </div>
      <div className="timeline__track">
        {!cascade ? (
          <p className="timeline__hint">
            {ready ? 'The cascade trips the most overloaded line, re-solves, and repeats until the grid settles or splits.' : 'Drop a data center on Florida to begin.'}
          </p>
        ) : n === 0 ? (
          <p className="timeline__hint">Nothing to cascade — no line is over its limit.</p>
        ) : (
          <>
            <ol className="ticks" aria-label="Cascade steps">
              {Array.from({ length: n + 1 }, (_, i) => (
                <li key={i}>
                  <button
                    type="button"
                    className={`tick${i === step ? ' tick--now' : ''}${i < step ? ' tick--past' : ''}`}
                    onClick={() => setStep(i)}
                    aria-label={i === 0 ? 'Before the cascade' : `Step ${i}`}
                    aria-current={i === step ? 'step' : undefined}
                  />
                </li>
              ))}
            </ol>
            <input
              className="timeline__range"
              type="range"
              min={0}
              max={n}
              step={1}
              value={step}
              onChange={(e) => setStep(Number(e.target.value))}
              aria-label="Cascade step"
            />
            <p className="timeline__now" aria-live="polite">
              <strong>{step === 0 ? 'Step 0' : `Step ${step} of ${n}`}</strong> {describe(cur)}
              {cur && cur.lost_mw > 0 && <span className="muted"> · {fmt(cur.lost_mw)} MW of homes and businesses lost</span>}
            </p>
          </>
        )}
        <ErrorBanner error={cascadeError} />
      </div>
    </div>
  )
}
