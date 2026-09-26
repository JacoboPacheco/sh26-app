import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { assetUrl } from '../../api'
import { useOverload } from '../../store'
import { Badge, ErrorBanner, Loading } from '../../ui'
import AskSlot from './AskSlot'
import BriefingDoc from './BriefingDoc'
import Captions from './Captions'
import MapOverlay from './MapOverlay'
import Progress from './Progress'
import { Callout, SayCaption, Ticker } from './ShowChrome'
import ShowProblem from './ShowProblem'
import Slide from './Slide'
import './briefing.css'
import { cleanBody, notLive, rememberReplay } from './briefingApi'
import { dwellMs, mergeSolutions, optionsOf, quietDeck, withShow } from './showDeck'
import './show.css'
import { applyBase, extentPoints, loc, setStoreCase, stepIndexOf, transcriptText } from './stage'
import { T } from './text'
import useDeck from './useDeck'
import useNarration, { readMuted, reducedMotion } from './useNarration'
import { getDownload } from './voiceApi'

// THE REVIEW STAGE: a full-screen, slide-by-slide presentation of what happened, over the live map.
// The presenter voice drives it: slides advance when their narration ends, the analyst calls each
// cascade step while the map trips that line, the camera flies to each area as it is named, and the
// captions follow the words. "Full briefing" swaps the slide for the whole written report.
//
// It is also a broadcast: every slide moves (counters, plays sliding into a feed, options revealing and
// turning the map green) and a ticker of facts crawls along the bottom. A paused slide is the finished
// picture; playing it again replays its beat.
//   body       the case under review (CaseIn + optional preset)
//   loadReplay load the report's cascade into the map (a preset, a saved scenario, a route)
//   autoPlay   start narrating once the deck is in (the click that opened the stage counts as a gesture)
export default function ReviewStage({ body, onClose, autoPlay = false, short: startShort = false, startView = 'slides', startAsk = false, allowFixture = false, loadReplay = false }) {
  const o = useOverload()
  const [lang, setLang] = useState('en')
  const [cc, setCc] = useState(true)
  const [view, setView] = useState(startView)
  const [askOpen, setAskOpen] = useState(startAsk)
  const [transcript, setTranscript] = useState(false)
  const [dl, setDl] = useState({ open: false, data: null, error: null, busy: false })
  const [fx, setFx] = useState({ hl: [], hlTone: 'hl', fix: false, apply: null, wave: 0, rings: [] })
  const reduced = useMemo(() => reducedMotion(), [])
  const locked = useRef(false)
  const rootRef = useRef(null)
  const { report: baseReport, deck: baseDeck, error, fixture, retry, late } = useDeck(body, { allowFixture, locked })
  // the AI proposer's verified plans arrive after the stage opens: they replace the solutions (and the report's
  // fixes) until the show has reached them; after that the slides on screen stay as they are
  const reached = useRef(false)
  const bodyKey = body ? JSON.stringify(body) : ''
  const [mergedState, setMerged] = useState(null)
  useEffect(() => {
    if (late && !reached.current) setMerged({ ...late, key: bodyKey })
  }, [late, bodyKey])
  const merged = mergedState?.key === bodyKey ? mergedState : null // another case: its own plans, not these
  const report = merged?.report || baseReport
  // the show's own beats (the chain, "the problem") are added to the deck; once it plays its slides stay put
  const frozen = useRef(null)
  const fullDeck = useMemo(() => {
    if (!(frozen.current && locked.current)) frozen.current = withShow(baseDeck, report)
    return mergeSolutions(frozen.current, merged?.deck)
  }, [baseDeck, report, merged])
  // the short version (the presentation: the toll, the plays, the pause, the solutions, the bottom line) is the same deck, fewer slides
  const [short, setShort] = useState(startShort)
  const canShort = !!fullDeck?.short?.length && fullDeck.short.length < fullDeck.slides.length
  const textDeck = useMemo(
    () => (short && canShort ? { ...fullDeck, slides: fullDeck.slides.filter((s) => fullDeck.short.includes(s.id)) } : fullDeck),
    [fullDeck, short, canShort],
  )
  // without a voice the captions are read: the plays and the options caption themselves as they land
  const [quiet, setQuiet] = useState(readMuted)
  const deck = useMemo(() => (quiet ? quietDeck(textDeck) : textDeck), [quiet, textDeck])
  // what the solutions beats and the bottom line compare: the engine's verified fixes, best first
  const options = useMemo(() => (report ? optionsOf(report, (deck?.slides || []).find((s) => (s.kind || s.id) === 'fix')) : []), [report, deck])
  // what the show is doing beyond the slide itself: the play that just landed (scoreboard, banner), the
  // option on screen, the green layer on the map
  const [live, setLive] = useState({ play: 0, callout: null, say: null, option: null, optionCues: false, cueStep: 0, layer: null })

  const oRef = useRef(o)
  const reportRef = useRef(report)
  const deckRef = useRef(deck)
  const optionsRef = useRef(options)
  const langRef = useRef(lang)
  useEffect(() => {
    oRef.current = o
    reportRef.current = report
    deckRef.current = deck
    optionsRef.current = options
    langRef.current = lang
  })

  // ------------------------------------------------------------------ the map follows the slides
  const camera = useCallback((cam) => {
    const O = oRef.current
    if (!cam || cam.type === 'none') return
    if (cam.type === 'region') return O.mapRef.current?.reset()
    const pts = [...(cam.points || [])]
    ;(cam.line_ids || []).forEach((id) => {
      const b = O.branchById.get(Number(id))
      if (b) pts.push(O.subPos(b.from_sub), O.subPos(b.to_sub))
    })
    ;(cam.sub_ids || []).slice(0, 400).forEach((id) => pts.push(O.subPos(id)))
    const p = pts.filter(Boolean)
    if (p.length || cam.center) O.focus(p, cam.center || undefined)
  }, [])

  // entering slide i: the camera, the map's moment (calm / replay / final), what the overlay draws
  const enter = useCallback(
    (i, playing) => {
      const slide = deckRef.current?.slides?.[i]
      if (!slide) return
      const O = oRef.current
      const c = O.cascade
      const map = slide.map || {}
      const kind = slide.kind || slide.id
      const last = c?.steps?.length || 0
      // a storm's own step (n = 0: the damage, before anything overloads). A storm briefing's "calm"
      // moments show that damage, never an intact grid, and it opens on the whole region, not the campus.
      const storm = c ? stepIndexOf(c, 0) : null
      // the chain plays on the map as the blast itself (the show's cards follow the map's own clock)
      const blast = kind === 'chain' && playing && !reduced && !!c && last > 0
      if (c) {
        if (blast) {
          O.setStep(0)
          O.setPlaying(true)
        } else if (kind === 'chain' && reduced) O.setStep(last) // no blast to watch: the chain's finished picture
        else if (map.mode === 'replay') O.setStep(playing ? (map.step_from > 0 ? (stepIndexOf(c, map.step_from) ?? 0) : 0) : (stepIndexOf(c, map.step_to) ?? last))
        else if (map.mode === 'calm' || map.mode === 'cause' || map.mode === 'fix') O.setStep(storm ?? 0)
        else if (map.mode === 'final' || map.mode === 'restore') O.setStep(last)
      }
      if (blast) {
        const pts = extentPoints(c, O.branchById, O.subPos)
        if (pts.length) O.focus(pts, c.sub_lat != null ? [c.sub_lon, c.sub_lat] : undefined)
      } else camera(storm != null && slide.id === 'event' ? { type: 'region' } : slide.camera)
      if (playing && (kind === 'fix' || kind === 'bottom_line' || kind === 'no_fix')) reached.current = true
      const waves = reportRef.current?.recovery?.waves?.length || 0
      // a fix slide with options draws its own green per option; the older whole-slide overlay would double it
      const own = kind === 'fix' && optionsRef.current.length > 0
      setFx({
        hl: own ? [] : map.highlight_lines || [],
        hlTone: map.mode === 'fix' ? 'fix' : 'hl',
        fix: map.mode === 'fix' && !playing && !own, // playing: shown when the narration says so
        apply: map.apply || null,
        wave: map.mode === 'restore' ? (playing ? 0 : waves) : 0,
        rings: [],
      })
      const cues = (slide.narration?.[langRef.current] || []).flatMap((g) => g.cues || [])
      setLive({ play: 0, callout: null, say: null, option: null, optionCues: own && cues.some((x) => x.name === 'option'), cueStep: 0, layer: null })
    },
    [camera, reduced],
  )

  const focusArea = useCallback(
    (name) => {
      const O = oRef.current
      const key = String(name).toLowerCase()
      const a = reportRef.current?.areas?.find((x) => String(x.area).toLowerCase() === key)
      let pts = []
      let center = a?.center || null
      if (a?.bbox) pts = [[a.bbox[0], a.bbox[1]], [a.bbox[2], a.bbox[3]]]
      if (!pts.length) pts = (O.grid?.subs || []).filter((s) => String(s.area).toLowerCase() === key).map((s) => [s.lon, s.lat])
      if (!pts.length && !center) return
      if (!center) center = [pts.reduce((n, p) => n + p[0], 0) / pts.length, pts.reduce((n, p) => n + p[1], 0) / pts.length]
      if (!reduced) O.focus(pts, center)
      setFx((f) => ({ ...f, rings: [{ center, key: `${key}-${performance.now()}` }] }))
    },
    [reduced],
  )

  const onCue = useCallback(
    (name, value) => {
      const O = oRef.current
      if (name === 'step') {
        const c = O.cascade
        if (!c) return
        setLive((l) => ({ ...l, cueStep: Math.max(l.cueStep, Number(value) || 0) })) // the play cards follow the words too
        if (O.playing) return // the blast is playing on the map: a cue must not cut it short
        O.setStep(stepIndexOf(c, value) ?? Math.min(Math.max(0, Number(value) || 0), c.steps.length))
      } else if (name === 'option') {
        // the first option's cue may be 0 or 1: the smallest value in the fix slide's cues is the first option
        const vals = (deckRef.current?.slides || [])
          .filter((s) => (s.kind || s.id) === 'fix')
          .flatMap((s) => (s.narration?.[langRef.current] || []).flatMap((g) => g.cues || []))
          .filter((x) => x.name === 'option')
          .map((x) => Number(x.value))
        const base = vals.length ? Math.min(...vals) : 1
        setLive((l) => ({ ...l, option: { n: Math.max(0, (Number(value) || 0) - base), at: performance.now() } }))
      } else if (name === 'area') {
        focusArea(value)
        setLive((l) => ({ ...l, area: String(value).toLowerCase() })) // the slide lights the area being named
      }
      else if (name === 'line') setFx((f) => ({ ...f, hl: [...f.hl, Number(value)], hlTone: 'hl' }))
      else if (name === 'fix') setFx((f) => ({ ...f, fix: true }))
      else if (name === 'wave') {
        const n = Number(value) || 0
        const w = reportRef.current?.recovery?.waves?.find((x) => x.n === n)
        const areas = reportRef.current?.areas || []
        const rings = (w?.areas_relit || [])
          .map((nm) => areas.find((a) => a.area === nm)?.center)
          .filter(Boolean)
          .map((center, j) => ({ center, key: `w${n}-${j}` }))
        setFx((f) => ({ ...f, wave: n, rings }))
      }
    },
    [focusArea],
  )

  // how much longer a slide stays up after its words end: the show's beats run longer than the narration
  const replayEnd = useRef(0)
  const holdFor = useCallback((slide, elapsed) => {
    const O = oRef.current
    const dwell = dwellMs(slide, reportRef.current, langRef.current, optionsRef.current)
    // with reduced motion a beat is its finished picture: it stays up long enough to read, not to watch
    let need = (reduced ? Math.min(dwell, 7000) : dwell) - elapsed
    if ((slide.kind || slide.id) === 'chain') {
      const now = performance.now()
      if (O.playing) {
        replayEnd.current = now // the blast is still on the map: wait, then let the last play land
        need = Math.max(need, 400)
      } else if (now - replayEnd.current < 2600) need = Math.max(need, 2600 - (now - replayEnd.current))
    }
    return Math.max(0, need)
  }, [reduced])
  const narr = useNarration({ deck, lang, onCue, onEnter: enter, holdFor })
  const narrMuted = narr.muted
  useEffect(() => setQuiet(narrMuted), [narrMuted])
  // switching between the short and the full version starts from the first slide
  const shortSeen = useRef(short)
  const { goto } = narr
  useEffect(() => {
    if (shortSeen.current === short) return
    shortSeen.current = short
    goto(0)
  }, [short, goto])
  const { idx, playing } = narr
  useEffect(() => {
    if (playing) locked.current = true // Gemini's deck no longer replaces the one being played
  }, [playing])

  // a slide change while paused (and a new cascade in the map) re-stages the map
  const cascadeNow = o.cascade
  useEffect(() => {
    if (!narr.playingRef.current) enter(idx, false)
  }, [idx, deck, cascadeNow, enter, narr.playingRef])
  // the map's cascade landed while the show was already running (it is computed as the stage opens): stage it now
  useEffect(() => {
    if (cascadeNow && narr.playingRef.current) enter(idx, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cascadeNow])
  // pausing (or the end of the show) leaves the map on the slide's finished picture and stops a blast still playing
  useEffect(() => {
    if (!playing) enter(idx, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing])

  // autoplay once the deck is in (Play briefing); the report follows the deck by a moment, so wait for it briefly
  const [waited, setWaited] = useState(false)
  useEffect(() => {
    const id = setTimeout(() => setWaited(true), 2500)
    return () => clearTimeout(id)
  }, [])
  const autoDone = useRef(false)
  const { play } = narr
  useEffect(() => {
    if (!autoPlay || autoDone.current || !deck || (!report && !waited)) return
    autoDone.current = true
    play()
  }, [autoPlay, deck, report, waited, play])

  // a preset / saved scenario / route: its cascade goes into the map, paused before it starts
  const replayKey = useRef(null)
  useEffect(() => {
    if (!loadReplay || !body) return
    const key = JSON.stringify(cleanBody(body))
    if (replayKey.current === key) return
    const O = oRef.current
    if (!body.preset) {
      // a plain case: its cascade runs on the map, paused (the store's case is left alone: the app
      // shell follows a changed case to the workspace, which would close a briefing page)
      replayKey.current = key
      O.startCascade(cleanBody(body)).then(() => oRef.current.setStep(0))
    } else if (report?.replay) {
      // a catastrophe: the engine's cascade, as computed (more storm lines than the grid API takes)
      replayKey.current = key
      rememberReplay(report.replay, body)
      O.startCascade({}, Promise.resolve(report.replay)).then(() => oRef.current.setStep(0))
    }
  }, [loadReplay, body, report])

  // ------------------------------------------------------------------ chrome
  useEffect(() => {
    const el = document.documentElement
    el.classList.add('review-open')
    const prev = document.activeElement
    rootRef.current?.querySelector('[data-autofocus]')?.focus()
    return () => {
      el.classList.remove('review-open')
      if (prev?.isConnected) prev.focus?.()
    }
  }, [])

  const t = T[lang]
  // the slides failed (the writer isn't live, or it errored) but the engine answered: the written briefing alone
  const docOnly = !deck && !!report && !!error
  const slides = deck?.slides || []
  // the written briefing alone: the map shows where the incident ended, the whole region in view
  useEffect(() => {
    const O = oRef.current
    if (!docOnly || !cascadeNow) return
    O.setStep(cascadeNow.steps.length)
    O.mapRef.current?.reset()
  }, [docOnly, cascadeNow])
  const slide = slides[Math.min(idx, slides.length - 1)]
  const slideKind = slide?.kind || slide?.id

  // what the slides call back into: the scoreboard, the banner, the green layer on the map, the camera
  const stage = useMemo(
    () => ({
      setPlay: (n) => setLive((l) => (l.play === n ? l : { ...l, play: n })),
      callout: (c) => setLive((l) => ({ ...l, callout: c })),
      say: (x) => setLive((l) => ({ ...l, say: x })),
      layer: (x) => setLive((l) => ({ ...l, layer: x })),
      wave: (n) => setFx((f) => (f.wave === n ? f : { ...f, wave: n })),
      camera,
    }),
    [camera],
  )
  const animate = narr.run > 0 && !reduced // the show is running on this slide; false = the finished picture

  const base = useMemo(() => applyBase(cleanBody(body), report), [body, report])
  const apply = useCallback(
    (delta) => {
      if (!delta || !base) return
      const O = oRef.current
      const full = { ...base, ...delta }
      // the map's case becomes the reviewed case with the fix, then its cascade runs (and stays calm)
      setStoreCase(O, full, delta)
      onClose()
      O.startCascade(full)
    },
    [base, onClose],
  )
  // closing leaves the map where the incident ended (the review card shows again under it)
  const close = useCallback(() => {
    const O = oRef.current
    if (O.cascade) O.setStep(O.cascade.steps.length)
    onClose()
  }, [onClose])
  const onApply = base ? apply : null // a catastrophe's fixes are listed, not applied (too many lines out for the map)

  const askInput = useRef(null)
  const askBody = useMemo(() => cleanBody(body), [body])
  const openAsk = useCallback(() => {
    setAskOpen(true)
    setTimeout(() => (askInput.current || rootRef.current?.querySelector('.rs-ask input, .rs-ask textarea'))?.focus(), 0)
  }, [])

  // keyboard: → / PageDown next · ← / PageUp previous · Space play/pause · Esc close · C captions · L language · / ask
  const { next, prev, toggle } = narr
  useEffect(() => {
    const onKey = (e) => {
      const el = e.target
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(el?.tagName || '') || el?.isContentEditable
      if (e.key === 'Escape') {
        e.preventDefault()
        if (typing) return el.blur()
        if (transcript) return setTranscript(false)
        if (dl.open) return setDl((d) => ({ ...d, open: false }))
        return close()
      }
      if (e.key === 'Tab') return trapFocus(e, rootRef.current)
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return
      const onControl = /^(BUTTON|A)$/.test(el?.tagName || '')
      if (e.key === 'ArrowRight' || e.key === 'PageDown') {
        e.preventDefault()
        next()
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        e.preventDefault()
        prev()
      } else if (e.key === ' ' && !onControl) {
        e.preventDefault()
        toggle()
      } else if (e.key === 'c' || e.key === 'C') setCc((v) => !v)
      else if (e.key === 'l' || e.key === 'L') setLang((l) => (l === 'en' ? 'es' : 'en'))
      else if (e.key === '/') {
        e.preventDefault()
        openAsk()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [next, prev, toggle, close, openAsk, transcript, dl.open])

  const toggleDownload = async () => {
    if (dl.open) return setDl((d) => ({ ...d, open: false }))
    setDl({ open: true, data: null, error: null, busy: true })
    try {
      const data = await getDownload(deck.deck_key, lang)
      setDl({ open: true, data, error: null, busy: false })
    } catch (err) {
      setDl({ open: true, data: null, error: err, busy: false })
    }
  }

  // ------------------------------------------------------------------ what the map overlay draws
  const waves = useMemo(() => report?.recovery?.waves || [], [report])
  const overlay = useMemo(() => {
    const lines = []
    if (fx.hlTone === 'hl' || fx.fix) fx.hl.forEach((id) => lines.push({ id, tone: fx.hlTone }))
    if (fx.fix && fx.apply?.upgrades) Object.keys(fx.apply.upgrades).forEach((id) => lines.push({ id, tone: 'fix' }))
    waves.filter((w) => w.n <= fx.wave).forEach((w) => (Array.isArray(w.lines) ? w.lines : []).forEach((id) => lines.push({ id, tone: 'fix' })))
    ;(live.layer?.lines || []).forEach((l) => lines.push(l))
    const ghost = live.layer?.ghost || (fx.fix && fx.apply?.lat != null && fx.apply?.lon != null ? { lat: fx.apply.lat, lon: fx.apply.lon } : null)
    return { lines, ghost, rings: fx.rings }
  }, [fx, waves, live.layer])

  const voiceBadge = (() => {
    const p = narr.provider
    if (narr.muted) return null // the Sound button says so
    if (p === 'timer') return <Badge tone="warn">{narr.muted ? t.voiceMuted : t.voiceNone}</Badge>
    if (p === 'browser' || narr.segFellBack) return <Badge tone="warn">{t.voiceBrowser}</Badge>
    if (p === 'elevenlabs' || narr.voice?.configured) return <Badge>{narr.voice?.attribution || t.voiceEleven}</Badge>
    return <Badge tone="warn">{t.voiceBrowser}</Badge>
  })()

  const slidesView = view === 'slides' && !docOnly
  const caption = narr.caption
  return createPortal(
    <div
      className={`rs${view === 'document' ? ' rs--doc' : ''}${narr.playing ? ' rs--playing' : ''}${slideKind ? ` rs--on-${slideKind}` : ''}`}
      ref={rootRef}
      role="dialog"
      aria-modal="true"
      aria-label={deck?.title?.[lang] || 'Simulation briefing'}
    >
      <header className="rs-top">
        <div className="rs-top__row">
          <span className="rs-sim">{t.sim}</span>
          <h1 className="rs-title">{deck?.title?.[lang] || (lang === 'es' ? 'Simulacro informativo' : 'Simulation briefing')}</h1>
          <div className="rs-tools">
            <div className="rs-seg" role="group" aria-label="Language">
              {['en', 'es'].map((l) => (
                <button key={l} type="button" className="rs-seg__btn" aria-pressed={lang === l} onClick={() => setLang(l)} aria-label={l === 'en' ? 'English' : 'Español'}>
                  {l.toUpperCase()}
                </button>
              ))}
            </div>
            <button type="button" className="rs-tool" aria-pressed={cc} onClick={() => setCc((v) => !v)} aria-label={t.captions}>
              CC
            </button>
            <button type="button" className="rs-tool" aria-pressed={!narr.muted} onClick={() => narr.setMuted(!narr.muted)} aria-label={t.sound}>
              {narr.muted ? `${t.sound}: off` : `${t.sound}: on`}
            </button>
            {voiceBadge}
            <button type="button" className="rs-tool" aria-pressed={transcript} onClick={() => setTranscript((v) => !v)} disabled={!deck}>
              {t.transcript}
            </button>
            <div className="rs-dl">
              <button type="button" className="rs-tool" aria-expanded={dl.open} onClick={toggleDownload} disabled={!deck}>
                {t.download}
              </button>
              {dl.open && <DownloadMenu dl={dl} deck={textDeck} lang={lang} />}
            </div>
            <div className="rs-seg" role="group" aria-label="View">
              <button type="button" className="rs-seg__btn" aria-pressed={view === 'slides'} onClick={() => setView('slides')}>
                {t.slides}
              </button>
              <button type="button" className="rs-seg__btn" aria-pressed={view === 'document'} onClick={() => setView('document')}>
                {t.document}
              </button>
            </div>
            <button type="button" className="rs-close" onClick={close} aria-label={t.close}>
              ×
            </button>
          </div>
        </div>
        {slides.length > 0 && <Progress slides={slides} idx={idx} progress={narr.progress} lang={lang} onJump={narr.goto} playing={narr.playing} />}
        <p className="rs-banner">{loc(deck, 'banner', lang) || report?.banner || 'SIMULATION · synthetic grid model · every people and cost number is an estimate.'}</p>
      </header>

      {slideKind !== 'problem' || !slidesView ? (
        <main className={view === 'document' || docOnly ? 'rs-panel rs-panel--doc' : 'rs-panel'}>
          {docOnly ? (
            <>
              {!notLive(error) && <ErrorBanner error={new Error('The slides could not be prepared; here is the written briefing.')} onRetry={retry} />}
              <BriefingDoc report={report} deck={null} lang={lang} stepIdx={(n) => stepIndexOf(o.cascade, n)} onApply={onApply} fixture={fixture} />
            </>
          ) : error ? (
            <ErrorBanner error={error} onRetry={retry} />
          ) : !deck ? (
            <Loading label={t.preparing} />
          ) : view === 'document' ? (
            <BriefingDoc report={report} deck={deck} lang={lang} stepIdx={(n) => stepIndexOf(o.cascade, n)} onApply={onApply} fixture={fixture} />
          ) : (
            slide && (
              <Slide
                key={`${slide.id}-${lang}-${narr.run}`}
                slide={slide}
                report={report}
                deck={deck}
                lang={lang}
                wave={fx.wave}
                onApply={onApply}
                fixture={fixture}
                stage={stage}
                live={live}
                animate={animate}
                options={options}
              />
            )
          )}
        </main>
      ) : (
        <ShowProblem key={`problem-${lang}-${narr.run}`} report={report} deck={deck} lang={lang} animate={animate} />
      )}

      {cc && slidesView && (caption ? <Captions caption={caption} lang={lang} reduced={reduced} /> : <SayCaption say={live.say} lang={lang} />)}
      {slidesView && animate && <Callout callout={live.callout} />}

      <nav className="rs-controls" aria-label="Briefing controls">
        <button type="button" className="rs-ctl" onClick={narr.prev} disabled={!deck || idx === 0} aria-label={t.prev}>
          ‹
        </button>
        <button type="button" className="rs-ctl rs-ctl--play" onClick={narr.toggle} disabled={!deck} aria-label={playing ? t.pause : t.play} data-autofocus>
          {playing ? t.pause : t.play}
        </button>
        <button type="button" className="rs-ctl" onClick={narr.next} disabled={!deck || idx >= slides.length - 1} aria-label={t.next}>
          ›
        </button>
        <span className="rs-count" aria-live="polite">
          {slides.length ? `${idx + 1} / ${slides.length}` : ''}
        </span>
        {canShort && (
          <button type="button" className="rs-tool" aria-pressed={short} onClick={() => setShort((v) => !v)} title={t.shortHint}>
            {t.short}
          </button>
        )}
        <button type="button" className="rs-tool" aria-expanded={askOpen} onClick={() => (askOpen ? setAskOpen(false) : openAsk())}>
          {t.ask}
        </button>
      </nav>

      {askOpen && (
        <aside className="rs-ask" aria-label={t.ask}>
          <div className="rs-ask__head">
            <strong>{t.ask}</strong>
            <button type="button" className="rs-close" onClick={() => setAskOpen(false)} aria-label="Close">
              ×
            </button>
          </div>
          <AskSlot caseBody={askBody} lang={lang} onLangChange={setLang} inputRef={askInput} note={t.askSoon} />
        </aside>
      )}

      {transcript && textDeck && <Transcript deck={textDeck} lang={lang} onClose={() => setTranscript(false)} />}

      {slidesView && deck && <Ticker report={report} deck={deck} lang={lang} playing={narr.playing} />}
      <MapOverlay lines={overlay.lines} ghost={overlay.ghost} rings={overlay.rings} layerKey={live.layer?.key || ''} />
    </div>,
    document.body,
  )
}

function trapFocus(e, root) {
  if (!root) return
  const els = [...root.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), textarea, select, [tabindex]:not([tabindex="-1"])')].filter(
    (x) => x.offsetParent !== null,
  )
  if (!els.length) return
  const first = els[0]
  const last = els[els.length - 1]
  if (!root.contains(document.activeElement)) {
    e.preventDefault()
    first.focus()
  } else if (e.shiftKey && document.activeElement === first) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault()
    first.focus()
  }
}

function Transcript({ deck, lang, onClose }) {
  const t = T[lang]
  return (
    <section className="rs-transcript" role="region" aria-label={t.transcript}>
      <div className="rs-ask__head">
        <strong>{t.transcript}</strong>
        <button type="button" className="rs-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <p className="rs-transcript__banner">{loc(deck, 'banner', lang)}</p>
      <ol>
        {deck.slides.map((s) => (
          <li key={s.id}>
            <h3>{s.headline?.[lang]}</h3>
            {(s.narration?.[lang] || []).map((g) => (
              <p key={g.key}>
                <span className="rs-transcript__who">{g.role === 'analyst' ? t.analyst : t.presenter}</span> {g.text}
              </p>
            ))}
          </li>
        ))}
      </ol>
    </section>
  )
}

function DownloadMenu({ dl, deck, lang }) {
  const t = T[lang]
  const local = useMemo(() => {
    const blob = new Blob([transcriptText(deck, lang)], { type: 'text/plain;charset=utf-8' })
    return URL.createObjectURL(blob)
  }, [deck, lang])
  useEffect(() => () => URL.revokeObjectURL(local), [local])
  const d = dl.data
  const region = String(deck.region || 'fl').toLowerCase()
  return (
    <div className="rs-menu" role="menu">
      {dl.busy && <Loading label="…" />}
      {d?.mp3_url ? (
        <a role="menuitem" href={assetUrlSafe(d.mp3_url)} download>
          {t.mp3}
        </a>
      ) : (
        !dl.busy && <p className="rs-menu__note">{t.noAudio}</p>
      )}
      {d?.vtt_url && (
        <a role="menuitem" href={assetUrlSafe(d.vtt_url)} download>
          {t.vtt}
        </a>
      )}
      {d?.txt_url ? (
        <a role="menuitem" href={assetUrlSafe(d.txt_url)} download>
          {t.txt}
        </a>
      ) : (
        !dl.busy && (
          <a role="menuitem" href={local} download={`overload-briefing-${region}-${lang}.txt`}>
            {t.txtLocal}
          </a>
        )
      )}
    </div>
  )
}

const assetUrlSafe = (p) => (/^https?:/.test(p) ? p : assetUrl(p))
