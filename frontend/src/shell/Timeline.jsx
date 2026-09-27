import { useLayoutEffect, useMemo, useRef } from 'react'
import { useOverload } from '../store'
import { Button, ErrorBanner } from '../ui'
import { useCascadeCueShown } from './CascadeCue'
import { buildSchedule } from './cascadeSchedule'

// The bottom bar: the one place the cascade is started, played and scrubbed, in every mode. It is only the
// replay's controls and its progress: the steps themselves play live in the results column (shell/StepFeed).
// The bar is the replay's clock: each step's mark sits where that step ENDS in the blast schedule
// (a step that hits many towns takes longer than a crack), and while the replay plays the fill runs
// with it to the end; paused, it sits exactly on the step.
export default function Timeline() {
  const o = useOverload()
  const { cascade, cascading, cascadeError, step, setStep, playing, setPlaying, startCascade, result, branchById, subById, fx } = o
  const ready = !!result // a solved case: a data center, a hurricane track, a heat level…
  const n = cascade?.steps.length || 0
  const live = !!(fx && playing)
  // the first run's button sits beside the dropped data center while that one is on screen (shell/CascadeCue)
  const cue = useCascadeCueShown() && !cascade && !cascading

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

  return (
    <div className="timeline">
      <div className="timeline__cta">
        {!cue && (
          <Button onClick={() => startCascade()} busy={cascading} disabled={!ready}>
            {cascading ? 'Running…' : cascade ? 'Run it again' : 'Run the cascade'}
          </Button>
        )}
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
            {cue
              ? 'Press Run the cascade beside the data center: it trips the most overloaded line, re-solves, and repeats until the grid settles or splits.'
              : ready ? 'The cascade trips the most overloaded line, re-solves, and repeats until the grid settles or splits.' : `Drop a data center on ${o.region === 'US' ? 'a state' : o.grid?.meta?.region_name || 'the map'} to begin.`}
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
          </>
        )}
        <ErrorBanner error={cascadeError} />
      </div>
    </div>
  )
}
