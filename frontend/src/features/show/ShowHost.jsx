import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { reducedMotion } from '../briefing/useNarration'
import ShowEngine from './engine'
import { createMap, regionBounds } from './mapCanvas'
import Icon from './Icon'
import ShowPlayer from './ShowPlayer'
import { listEpisodes, loadShow } from './showApi'
import './show.css'

// "Watch the story": one full-screen stage from the moment it opens. The map is always there (the lower 48 while
// you pick, the episode's region while the Gemini director writes it, then the episode itself), so picking,
// loading and playing are one continuous shot. Escape closes; focus returns to the button that opened it.

const LOWER48 = [[24.2, -125.0], [49.5, -66.5]]
const noSub = () => () => {}
const noVersion = () => 0

export default function ShowHost({ initial, onClose }) {
  const [phase, setPhase] = useState(initial ? 'load' : 'pick') // pick | load | play
  const [episodes, setEpisodes] = useState(null)
  const [epError, setEpError] = useState(null)
  const [choice, setChoice] = useState(initial ? { id: initial, title: '' } : null)
  const [lang, setLang] = useState('en')
  const [progress, setProgress] = useState(initial ? { step: 0, of: 0, text: '', steps: [] } : null) // {step, of, text, estimate_s, steps: [text]}
  const [loadError, setLoadError] = useState(null)
  const [show, setShow] = useState(null)
  const [engine, setEngine] = useState(null)
  const [reduced] = useState(reducedMotion)
  const rootRef = useRef(null)
  const canvasRef = useRef(null)
  const progressRef = useRef(null)
  const phaseRef = useRef({ phase, choice, engine, episodes, since: 0, run: -1 })

  // ------------------------------------------------------------------ the page under the stage
  useEffect(() => {
    document.documentElement.classList.add('shw-open')
    const prev = document.activeElement
    rootRef.current?.focus()
    return () => {
      document.documentElement.classList.remove('shw-open')
      prev?.focus?.()
    }
  }, [])

  useEffect(() => {
    let live = true
    listEpisodes()
      .then((eps) => live && setEpisodes(eps))
      .catch((e) => live && setEpError(e.message))
    return () => {
      live = false
    }
  }, [])

  // ------------------------------------------------------------------ loading an episode
  const loadToken = useRef(0)
  useEffect(() => {
    if (phase !== 'load' || !choice) return undefined
    const counter = loadToken // the counter object itself: bumping it in the cleanup ends this load's polling
    const token = ++counter.current
    const alive = () => token === counter.current
    const region = choice.region
    loadShow(choice.id, { region: region && region.length === 2 ? region : undefined, lang }, (p) => {
      if (!alive()) return
      setProgress((old) => {
        const steps = old?.steps || []
        return { ...p, steps: p.text && steps[steps.length - 1] !== p.text ? [...steps, p.text].slice(-6) : steps }
      })
    }, alive)
      .then((s) => {
        if (!alive() || !s) return
        if (!s.scenes?.length) throw new Error('This episode came back empty. Try again.')
        setShow(s)
        const e = new ShowEngine(s)
        setEngine(e)
        setPhase('play')
        e.play() // the visuals and captions start at once; audio only if this viewer turned sound on before
      })
      .catch((err) => alive() && setLoadError(err.message || 'The episode could not be made.'))
    return () => {
      counter.current++
    }
  }, [phase, choice, lang])

  useEffect(() => () => engine?.close(), [engine])

  // Escape closes while picking or loading (the player handles its own keys)
  useEffect(() => {
    if (phase === 'play') return undefined
    const on = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [phase, onClose])

  const pick = useCallback(
    (ep) => {
      engine?.close()
      setEngine(null)
      setChoice(ep)
      setShow(null)
      setLoadError(null)
      setProgress({ step: 0, of: 0, text: '', steps: [] })
      setPhase('load')
    },
    [engine],
  )
  const backToPicker = useCallback(() => {
    loadToken.current++
    engine?.close()
    setEngine(null)
    setShow(null)
    setPhase('pick')
  }, [engine])

  // ------------------------------------------------------------------ the map and the clock
  useEffect(() => {
    phaseRef.current = { ...phaseRef.current, phase, choice, engine, episodes }
    if (phase !== 'play') phaseRef.current.since = performance.now()
  }, [phase, choice, engine, episodes])

  useEffect(() => {
    const cv = canvasRef.current
    if (!cv) return undefined
    const map = createMap(cv, { reduced })
    let raf = 0
    let last = performance.now()
    const loop = (now) => {
      const dt = now - last
      last = now
      const st = phaseRef.current
      const e = st.engine
      if (st.phase === 'play' && e) {
        e.tick(dt)
        map.draw({ show: e.show, scene: e.scene, idx: e.idx, run: e.run, t: e.t, est: e.estMs, now })
        // the current chapter's fill, every frame (no re-render)
        const fill = progressRef.current
        if (fill) {
          const start = Number(fill.dataset.start)
          const count = Number(fill.dataset.count) || 1
          fill.style.transform = `scaleX(${Math.min(1, (e.idx - start + (e.ended ? 1 : e.sceneProgress)) / count)})`
        }
      } else {
        // picking: the lower 48, the grid off; loading: the episode's region, its grid dim
        const loading = st.phase === 'load' && st.choice
        const region = loading ? st.choice.region || 'FL' : 'US'
        const scene = loading ? { panel: true, camera: { bounds: regionBounds(region) }, layers: [{ type: 'grid', mode: 'dim' }] } : { panel: true, camera: { bounds: LOWER48 }, layers: [{ type: 'grid', mode: 'hidden' }] }
        map.draw({ show: { region }, scene, idx: 0, run: loading ? -2 : -1, t: now - st.since, est: 60000, now })
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      map.destroy()
    }
  }, [reduced])

  const epIdx = episodes ? episodes.findIndex((e) => e.id === choice?.id) : -1
  const nextEpisode = epIdx >= 0 ? episodes[epIdx + 1] || null : null

  // the stage's paused class follows the engine (it freezes every CSS animation on the overlays)
  useSyncExternalStore(engine ? engine.subscribe : noSub, engine ? engine.getVersion : noVersion)
  const playing = engine?.playing
  return (
    <div
      className={`shw${phase === 'play' && !playing && !engine?.ended ? ' is-paused' : ''}${reduced ? ' is-reduced' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={show ? `Watch the story: ${show.title}` : 'Watch the story'}
      data-scene={phase === 'play' ? engine?.scene?.id || '' : ''}
      tabIndex={-1}
      ref={rootRef}
    >
      <canvas className="shw-map" ref={canvasRef} aria-hidden="true" />
      <header className="shw-top">
        <p className="shw-top__brand">Overload</p>
        {show && <p className="shw-top__title">{show.title}</p>}
        <button type="button" className="shw-btn shw-btn--icon shw-top__close" onClick={onClose} aria-label="Close the story (Escape)">
          <Icon name="close" />
        </button>
      </header>

      {phase === 'pick' && <Picker episodes={episodes} error={epError} lang={lang} setLang={setLang} onPick={pick} />}
      {phase === 'load' && <Loading choice={choice} episodes={episodes} progress={progress} error={loadError} onRetry={() => pick({ ...choice })} onBack={backToPicker} />}
      {phase === 'play' && engine && show && (
        <ShowPlayer
          engine={engine}
          show={show}
          reduced={reduced}
          onClose={onClose}
          onPick={backToPicker}
          next={nextEpisode}
          onNext={() => nextEpisode && pick(nextEpisode)}
          progressRef={progressRef}
        />
      )}
    </div>
  )
}

// ------------------------------------------------------------------ the episode picker
function Picker({ episodes, error, lang, setLang, onPick }) {
  return (
    <section className="shw-pick" aria-labelledby="shw-pick-title">
      <h2 id="shw-pick-title" className="shw-pick__title">Watch the story</h2>
      <p className="shw-pick__lede">
        Narrated episodes on a synthetic model of the grid. A Gemini agent writes each one from the engine&apos;s numbers and every line is checked;
        ElevenLabs gives it a voice when you turn sound on.
      </p>
      {error && <p className="shw-pick__error">The episodes did not load: {error}</p>}
      {!episodes && !error && <p className="shw-pick__wait">Loading the episodes…</p>}
      {episodes && (
        <ol className="shw-pick__list">
          {episodes.map((ep, i) => (
            <li key={ep.id}>
              <button type="button" className="shw-ep" onClick={() => onPick(ep)}>
                <span className="shw-ep__n" aria-hidden="true">
                  {i + 1}
                </span>
                <span className="shw-ep__body">
                  <span className="shw-ep__title">{ep.title}</span>
                  <span className="shw-ep__blurb">{ep.blurb}</span>
                </span>
                {ep.est_minutes ? <span className="shw-ep__min">{Math.round(ep.est_minutes)} min</span> : null}
              </button>
            </li>
          ))}
        </ol>
      )}
      <div className="shw-pick__foot">
        <label className="shw-pick__lang">
          <span>Language</span>
          <select value={lang} onChange={(e) => setLang(e.target.value)}>
            <option value="en">English</option>
            <option value="es">Español</option>
          </select>
        </label>
        <p className="shw-pick__sound">Sound stays off until you turn it on in the player.</p>
      </div>
    </section>
  )
}

// ------------------------------------------------------------------ while the director writes
function Loading({ choice, episodes, progress, error, onRetry, onBack }) {
  const ep = episodes?.find((e) => e.id === choice?.id) || choice || {}
  const pct = progress?.of ? Math.min(1, progress.step / progress.of) : null
  return (
    <section className="shw-load" aria-live="polite" aria-busy={!error}>
      <p className="shw-load__kicker">Episode</p>
      <h2 className="shw-load__title">{ep.title || 'Preparing the episode'}</h2>
      {error ? (
        <>
          <p className="shw-load__error">{error}</p>
          <div className="shw-load__actions">
            <button type="button" className="shw-btn shw-btn--primary" onClick={onRetry}>
              Try again
            </button>
            <button type="button" className="shw-btn" onClick={onBack}>
              Other episodes
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="shw-load__line">
            The Gemini director is writing the episode<span className="shw-dots" aria-hidden="true"><i /><i /><i /></span>
          </p>
          <div className="shw-load__bar" aria-hidden="true">
            <span className={pct == null ? 'is-indeterminate' : ''} style={pct == null ? undefined : { transform: `scaleX(${pct})` }} />
          </div>
          <ol className="shw-load__steps">
            {(progress?.steps || []).map((s, i, all) => (
              <li key={`${i}${s}`} className={i === all.length - 1 ? 'is-now' : 'is-done'}>
                {s}
              </li>
            ))}
          </ol>
          {progress?.estimate_s ? <p className="shw-load__est">Usually about {Math.max(5, Math.round(progress.estimate_s))} seconds. A finished episode starts at once next time.</p> : null}
          <button type="button" className="shw-btn shw-load__back" onClick={onBack}>
            Other episodes
          </button>
        </>
      )}
    </section>
  )
}

