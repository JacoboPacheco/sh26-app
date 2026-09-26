import { useLayoutEffect, useMemo, useRef } from 'react'
import { fmt } from '../geo'
import { useOverload } from '../store'
import { Button, ErrorBanner } from '../ui'
import { buildSchedule, titleCase } from './cascadeSchedule'

// The bottom bar: the one place the cascade is started, played and scrubbed, in every mode.
// The bar is the replay's clock: each step's mark sits where that step ENDS in the blast schedule
// (a step that hits many towns takes longer than a crack), and while the replay plays the fill runs
// with it to the end; paused, it sits exactly on the step.
export default function Timeline() {
  const o = useOverload()
  const { cascade, cascading, cascadeError, step, setStep, playing, setPlaying, startCascade, result, subName, branchById, subById, fx } = o
  const ready = !!result // a solved case: a data center, a hurricane track, a heat level…
  const n = cascade?.steps.length || 0
  const live = !!(fx && playing)
  // while the replay plays, the line names the step being played (the store's step moves at its end)
  const shown = live ? Math.min(step + 1, n) : step
  const cur = cascade && shown > 0 ? cascade.steps[shown - 1] : null

  // where each step's mark sits (0..1): the end of its tier in the whole replay
  const marks = useMemo(() => {
    if (!n) return []
    const s = buildSchedule(cascade, subById, branchById, 0)
    return [0, ...s.tiers.map((t) => (s.total ? t.t1 / s.total : 1))]
  }, [cascade, n, subById, branchById])

  const fillRef = useRef(null)
  const key = live ? fx : `step-${step}`
  useLayoutEffect(() => {
    const el = fillRef.current
    if (!el || !marks.length) return
    const at = (i) => `${(marks[Math.min(i, marks.length - 1)] || 0) * 100}%`
    el.style.transition = 'none'
    el.style.width = at(live ? fx.from : step)
    if (!live) return
    void el.offsetWidth // commit the start before the transition runs
    const left = Math.max(0, fx.schedule.total - (performance.now() - fx.startedAt))
    el.style.transition = `width ${Math.round(left)}ms linear`
    el.style.width = '100%'
  }, [key, marks]) // eslint-disable-line react-hooks/exhaustive-deps -- key covers fx / step / live

  function describe(st) {
    if (!st) return 'Before anything trips'
    const names = st.tripped.slice(0, 2).map((id) => {
      const b = branchById.get(id)
      if (!b) return `line ${id}`
      const name = (id) => titleCase(subName(id))
      return b.from_sub === b.to_sub ? `${name(b.from_sub)} transformer` : `${name(b.from_sub)} → ${name(b.to_sub)}`
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
            {ready ? 'The cascade trips the most overloaded line, re-solves, and repeats until the grid settles or splits.' : `Drop a data center on ${o.region === 'US' ? 'a state' : o.grid?.meta?.region_name || 'the map'} to begin.`}
          </p>
        ) : n === 0 ? (
          <p className="timeline__hint">Nothing to cascade — no line is over its limit.</p>
        ) : (
          <>
            {/* one continuous bar: the fill runs with the replay's clock */}
            <div className="flowbar">
              <div className="flowbar__fill" ref={fillRef} />
              <ol className="flowbar__marks" aria-label="Cascade steps">
                {Array.from({ length: n + 1 }, (_, i) => (
                  <li key={i} style={{ left: `${(marks[i] ?? i / n) * 100}%` }}>
                    <button
                      type="button"
                      className={`mark${i === step ? ' mark--now' : ''}${i < step ? ' mark--past' : ''}`}
                      onClick={() => setStep(i)}
                      aria-label={i === 0 ? 'Before the cascade' : `Step ${i}`}
                      aria-current={i === step ? 'step' : undefined}
                    />
                  </li>
                ))}
              </ol>
            </div>
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
              <strong>{shown === 0 ? 'Step 0' : `Step ${shown} of ${n}`}</strong> {describe(cur)}
              {cur && cur.lost_mw > 0 && <span className="muted"> · {fmt(cur.lost_mw)} MW of homes and businesses lost</span>}
            </p>
          </>
        )}
        <ErrorBanner error={cascadeError} />
      </div>
    </div>
  )
}
