import { useEffect, useMemo, useRef, useState } from 'react'
import SceneOverlays from './Overlays'
import Icon from './Icon'
import ShowCaptions from './ShowCaptions'
import { actorName, chaptersOf, useEngine } from './util'
import { wordsFor } from './words'

// The player's chrome and layers over the stage's map: the scene overlays, the captions, the controls (play, the
// scenes, the chapter bar, sound), the "Made by a Gemini agent" sheet and the end card. The stage (ShowHost) owns the
// canvas and the animation loop; this renders from the engine's discrete state.

const AI_LINE = {
  gemini: (ai) =>
    `Written by a Gemini agent${ai.model ? ` (${ai.model})` : ''} from the engine's numbers; every line checked against the fact sheet.` +
    (ai.replayed ? ` Saved${ai.saved_at ? ` on ${String(ai.saved_at).replace('T', ' at ').slice(0, 19)}` : ''} and replayed: no new Gemini calls for this viewing.` : ''),
  fallback: () => 'Gemini was not available, so a fallback script wrote this episode from the same engine numbers.',
  not_configured: () => 'Gemini is not configured on this server: a template script wrote this episode from the engine numbers.',
}

export default function ShowPlayer({ engine, show, reduced, onClose, onPick, next, onNext, progressRef }) {
  useEngine(engine)
  const scenes = engine.scenes
  const scene = engine.scene
  const idx = engine.idx
  const chapters = useMemo(() => chaptersOf(scenes), [scenes])
  const ci = chapters.findIndex((c) => idx >= c.start && idx < c.start + c.count)
  const [aiOpen, setAiOpen] = useState(false)

  // the last scene's overlays stay a moment while they fade out
  const [leaving, setLeaving] = useState(null) // {scene, key}
  const lastRef = useRef({ scene, run: engine.run })
  useEffect(() => {
    const last = lastRef.current
    lastRef.current = { scene, run: engine.run }
    if (last.run === engine.run || !last.scene) return undefined
    setLeaving({ scene: last.scene, key: last.run })
    const id = setTimeout(() => setLeaving(null), 380)
    return () => clearTimeout(id)
  }, [engine.run, scene])
  const prevScene = idx > 0 ? scenes[idx - 1] : null

  // the band above the captions (lower thirds, the timeline) and the right column stop where the captions begin,
  // whatever the captions' height on this screen
  const bottomRef = useRef(null)
  useEffect(() => {
    const el = bottomRef.current
    const stage = el?.closest('.shw')
    const cap = el?.querySelector('.shw-cap, .shw-controls')
    if (!el || !stage) return undefined
    const set = () => {
      const top = (el.querySelector('.shw-cap') || cap || el).getBoundingClientRect().top
      stage.style.setProperty('--sh-bottom', `${Math.max(120, Math.round(window.innerHeight - top + 12))}px`)
    }
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    window.addEventListener('resize', set)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', set)
    }
  }, [engine.ended])

  // keyboard: space plays/pauses, arrows move a scene, m sound, Escape closes the sheet or the player
  useEffect(() => {
    const on = (e) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return
      const tag = e.target?.tagName
      // like a video player: Space plays and pauses even right after a click on the transport (sound, chapters);
      // anywhere else a focused button keeps its own Space
      const transport = !!e.target?.closest?.('.shw-controls')
      const control = !transport && (tag === 'BUTTON' || tag === 'A' || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA')
      if (e.key === 'Escape') {
        e.preventDefault()
        if (aiOpen) setAiOpen(false)
        else onClose()
      } else if ((e.key === ' ' || e.key === 'k') && !control) {
        e.preventDefault()
        engine.toggle()
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        engine.next()
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        engine.prev()
      } else if (e.key === 'm') engine.setMuted(!engine.muted)
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [engine, aiOpen, onClose])

  const w = wordsFor(show.lang)
  const ai = show.ai || {}
  const aiLabel = ai.status === 'gemini' ? w.chipGemini : ai.status === 'fallback' ? w.chipFallback : w.chipTemplate
  const est = engine.estMs

  return (
    <>
      {leaving && <SceneOverlays key={`L${leaving.key}`} engine={null} scene={leaving.scene} est={est} leaving words={w} />}
      {!engine.ended && <SceneOverlays key={`S${engine.run}`} engine={engine} scene={scene} prevScene={prevScene} est={est} words={w} />}

      <div className="shw-bottom" ref={bottomRef}>
        {!engine.ended && <ShowCaptions caption={engine.caption} audio={engine.audio} reduced={reduced} voices={show.voices} words={w} />}
        <div className="shw-controls" role="group" aria-label="Playback">
          <div className="shw-controls__buttons">
            <button type="button" className="shw-btn shw-btn--icon" onClick={engine.prev} aria-label="Previous scene" disabled={idx === 0 && engine.t < 2500}>
              <Icon name="prev" />
            </button>
            <button type="button" className="shw-btn shw-btn--play" onClick={engine.toggle} aria-label={engine.playing ? 'Pause' : 'Play'}>
              <Icon name={engine.playing ? 'pause' : 'play'} />
            </button>
            <button type="button" className="shw-btn shw-btn--icon" onClick={engine.next} aria-label="Next scene" disabled={idx >= scenes.length - 1}>
              <Icon name="next" />
            </button>
          </div>
          <ol className="shw-chapters" aria-label="Chapters">
            {chapters.map((c, i) => (
              <li key={i} className={`shw-chapter${i === ci ? ' is-on' : ''}${i < ci || engine.ended ? ' is-done' : ''}`} style={{ flexGrow: c.count }}>
                <button type="button" onClick={() => engine.goto(c.start)} aria-label={`Chapter ${i + 1} of ${chapters.length}: ${c.name}`} aria-current={i === ci ? 'step' : undefined}>
                  <span className="shw-chapter__bar">
                    {/* the current chapter's fill is set every frame by the stage's loop; the others are full or empty */}
                    <span
                      className="shw-chapter__fill"
                      ref={i === ci && !engine.ended ? progressRef : undefined}
                      data-start={c.start}
                      data-count={c.count}
                      style={i === ci && !engine.ended ? undefined : { transform: `scaleX(${i < ci || engine.ended ? 1 : 0})` }}
                    />
                  </span>
                  <span className="shw-chapter__name">{c.name}</span>
                </button>
              </li>
            ))}
          </ol>
          <button
            type="button"
            className={`shw-btn shw-btn--sound${engine.muted ? '' : ' is-on'}`}
            onClick={() => engine.setMuted(!engine.muted)}
            aria-pressed={!engine.muted}
            title={engine.muted ? w.soundOffTip : w.soundOnTip}
          >
            <Icon name={engine.muted ? 'muted' : 'sound'} />
            <span>{engine.muted ? w.soundOff : w.soundOn}</span>
          </button>
        </div>
        <p className="shw-note">{show.note || 'Synthetic grid model (Breakthrough Energy / Texas A&M): estimates, not a prediction about any real utility or project.'}</p>
      </div>

      <button type="button" className={`shw-aichip shw-aichip--${ai.status || 'none'}`} onClick={() => setAiOpen(true)} aria-haspopup="dialog">
        <span className="shw-aichip__dot" aria-hidden="true" />
        {aiLabel}
        {show.fixture && <span className="shw-aichip__fx">fixture</span>}
      </button>

      {engine.ended && <div className="shw-endscrim" aria-hidden="true" />}
      {engine.ended && (
        <section className="shw-end" aria-label="End of episode">
          <p className="shw-end__kicker">{w.end}</p>
          <h2 className="shw-end__title">{show.title}</h2>
          <div className="shw-end__actions">
            {next && (
              <button type="button" className="shw-btn shw-btn--primary" onClick={onNext}>
                {w.next}: {next.title}
              </button>
            )}
            <button type="button" className={`shw-btn${next ? '' : ' shw-btn--primary'}`} onClick={() => engine.play()}>
              {w.again}
            </button>
            <button type="button" className="shw-btn" onClick={onPick}>
              {w.another}
            </button>
            <button type="button" className="shw-btn" onClick={onClose}>
              {w.close}
            </button>
          </div>
          {show.sources?.length > 0 && (
            <ul className="shw-end__sources">
              {show.sources.map((s, i) => (
                <li key={i}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {aiOpen && <AiSheet show={show} ai={ai} onClose={() => setAiOpen(false)} />}
    </>
  )
}

// ------------------------------------------------------------------ how this episode was made
function AiSheet({ show, ai, onClose }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    const prev = document.activeElement
    el?.focus()
    return () => prev?.focus?.()
  }, [])
  const line = (AI_LINE[ai.status] || AI_LINE.not_configured)(ai)
  const stats = [
    ['Gemini calls', ai.calls],
    ['Answers replayed from cache', ai.cached_calls || null],
    ['Tools called', ai.tools_called],
    ['Drafts Gemini self-checked', ai.drafts_checked || null],
    ['Drafts sent back', ai.drafts_rejected || null],
    ['Lines checked', ai.lines_checked],
    ['Scenes rejected, then rewritten or replaced', ai.lines_rejected],
  ].filter(([, v]) => v != null)
  return (
    <aside className="shw-sheet" role="dialog" aria-modal="false" aria-labelledby="shw-sheet-title" tabIndex={-1} ref={ref}>
      <header className="shw-sheet__head">
        <h2 id="shw-sheet-title">How this episode was made</h2>
        <button type="button" className="shw-btn shw-btn--icon" onClick={onClose} aria-label="Close">
          <Icon name="close" />
        </button>
      </header>
      <p className="shw-sheet__line">{line}</p>
      {stats.length > 0 && (
        <dl className="shw-sheet__stats">
          {stats.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{Number(v).toLocaleString('en-US')}</dd>
            </div>
          ))}
        </dl>
      )}
      {ai.trace?.length > 0 && (
        <>
          <h3 className="shw-sheet__h">The director&apos;s trace</h3>
          <ol className="shw-sheet__trace">
            {ai.trace.map((s, i) => (
              <li key={i} className={`shw-step shw-step--${s.actor === 'engine' ? 'engine' : 'gemini'} shw-step--${s.kind}`}>
                <span className="shw-step__who">
                  {actorName(s.actor)} <span className="shw-step__kind">{s.kind}</span>
                </span>
                <span className="shw-step__text">{s.text}</span>
              </li>
            ))}
          </ol>
        </>
      )}
      <h3 className="shw-sheet__h">Sources</h3>
      <ul className="shw-sheet__sources">
        {(show.sources || []).map((s, i) => (
          <li key={i}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}</li>
        ))}
        <li>Voice: ElevenLabs when sound is on and the voice service is configured; otherwise the browser&apos;s voice or captions.</li>
      </ul>
      <p className="shw-sheet__note">{show.note}</p>
    </aside>
  )
}
