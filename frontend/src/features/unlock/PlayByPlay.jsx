// "Watch it get built": the full-screen play-by-play over the map (StrengthenPage renders it as a direct child of
// .mc--unlock; strengthen's layout gives the whole page to the map while it is open, the top bar stays). The
// presenter's script (backend/narrate.py) is an intro, one beat per PACKAGE of upgrades (Gemini groups them, the
// engine checks the grouping) and a closing; about a minute whatever the budget.
//   top ........ Back to the plan (and Esc) · the beats (weak points, each package by name, the answer) · the
//                scoreboard (campuses at once, today -> now, a small meter; upgrades so far)
//   left ....... the beat's card: what this beat is about (the weak point and its loading today; the package, why
//                its upgrades belong together, what it costs; the answer)
//   bottom ..... who grouped the packages (Gemini, checked by the engine / the engine's grouping) and the captions
//                (heard only when this viewer turned sound on: MUTED), Pause / Play
//   the map .... PbpLayer: the camera flies to each package, its lines draw in green, the campuses drop in
// When the closing ends it lands on a clean final frame: the whole plan green, the page's answer sentence. Reduced
// motion: nothing plays by itself; each beat shows its finished frame and Next moves on.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { Button } from '../../ui'
import AiBadge from '../ai/AiBadge'
import { slideFrac } from '../briefing/useNarration'
import { money } from '../cost/money'
import { BuildCaption, getNarration } from '../narrate'
import { costAt, ordinal } from './capacity'
import { beatPackage, campusesLabel, ends, framed, packagesOf, problemsOf } from './pbp'
import { closePbp, endPbp, holdPbp, resumePbp, setPbpView, showCap } from './unlockStore'
import './playbyplay.css'

const WHY = {
  'AI off for this request': 'AI off',
  'Gemini not configured': 'Gemini not set up',
  'Gemini unavailable': 'Gemini unavailable',
  "Gemini's grouping failed its checks": 'Gemini’s grouping failed a check',
}

export default function PlayByPlay({ o, u, m, nb, body, answer, target }) {
  const still = !!nb.reduced
  // reduced motion: the viewer steps through the beats; the same script, fetched without the engine playing it
  const bodyKey = JSON.stringify(body)
  const [rs, setRs] = useState({ key: '', script: null, error: null })
  const [ri, setRi] = useState(0)
  useEffect(() => {
    if (!still) return undefined
    let live = true
    getNarration(JSON.parse(bodyKey)).then(
      (script) => live && setRs({ key: bodyKey, script, error: null }),
      (error) => live && setRs({ key: bodyKey, script: null, error }),
    )
    return () => {
      live = false
    }
  }, [still, bodyKey])

  const script = still ? (rs.key === bodyKey ? rs.script : null) : nb.stale ? null : nb.script
  const error = still ? (rs.key === bodyKey ? rs.error : null) : nb.status === 'error' ? nb.error : null
  const slides = useMemo(() => script?.slides || [], [script])
  const idx = Math.min(still ? ri : nb.idx, Math.max(0, slides.length - 1))
  const slide = slides[idx] || null
  const final = !!u.pbpEnd
  const pkgs = useMemo(() => packagesOf(m, script), [m, script])
  const K = pkgs.length
  const cur = beatPackage(slide, K, final)
  const goal = script?.bought?.n ?? target
  const problems = useMemo(() => problemsOf(m, script, goal), [m, script, goal])
  const shown = Math.min(u.capShown, m.steps.length)
  const loading = !script && !error
  const prog = still ? 1 : Math.max(0, Math.min(1, nb.progress || 0)) // how far the voice is through this beat

  // the current beat's bar fills CONTINUOUSLY from the narration's clock (never in per-word jumps): every frame
  // writes the fill straight to the element, like briefing/Progress.jsx. Still (reduced motion): it just stands full.
  const nowBarRef = useRef(null)
  useEffect(() => {
    const el = nowBarRef.current
    if (!el) return undefined
    if (still || !nb.playing || !nb.clock) {
      el.style.transform = `scaleX(${prog})`
      return undefined
    }
    let raf = 0
    let shown = prog
    let gen = null
    const tick = (now) => {
      const c = nb.clock.current
      const mine = !!c && c.idx === idx
      const t = mine ? slideFrac(c, now) : shown
      if (mine && c.gen !== gen) {
        gen = c.gen
        shown = t
      }
      shown = Math.max(shown, t)
      el.style.transform = `scaleX(${shown})`
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [still, nb.playing, nb.clock, idx, prog])

  // the map layer draws from the store
  useEffect(() => setPbpView(script, idx), [script, idx])

  // reduced motion: each beat stands finished (its campuses all in)
  useEffect(() => {
    if (still && slide && !final) showCap(Math.max(m.today, slide.step))
  }, [still, slide, final, m.today])

  // the camera: each beat frames what it is about; the closing and the final frame show the whole state
  const beatId = final ? 'final' : slide?.id || 'intro'
  const frame = useRef(null)
  useEffect(() => {
    frame.current = () => {
      if (beatId === 'final' || slide?.kind === 'close') return o.mapRef.current?.reset()
      const pk = slide?.kind === 'package' ? pkgs[slide.package - 1] : null
      const pts = pk
        ? [...pk.projects.flatMap(ends), ...pk.sites.map((st) => [st.site.lon, st.site.lat])]
        : [...problems.flatMap(ends), ...m.steps.slice(0, m.today).map((st) => [st.site.lon, st.site.lat])]
      if (pts.length) o.focus(framed(pts))
      else o.mapRef.current?.reset()
    }
  })
  useEffect(() => {
    frame.current?.()
  }, [beatId])

  // the top bar's real height (on a phone it wraps to two or three rows): the map and this overlay start under it
  useLayoutEffect(() => {
    const bar = document.querySelector('.appbar')
    const root = document.querySelector('.mc.mc--unlock')
    if (!bar || !root) return undefined
    // its content, not its box: the box is a fixed height its rows can outgrow on a narrow screen
    const set = () => {
      const r = bar.getBoundingClientRect()
      root.style.setProperty('--pbp-top', `${Math.round(Math.max(r.bottom, r.top + bar.scrollHeight))}px`)
    }
    set()
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(set) : null
    ro?.observe(bar)
    window.addEventListener('resize', set)
    return () => {
      ro?.disconnect()
      window.removeEventListener('resize', set)
      root.style.removeProperty('--pbp-top')
    }
  }, [])

  // keyboard focus moves into the mode (the button that opened it is hidden with the dashboard): Tab reaches the
  // play-by-play's own controls, Enter / Esc go back
  const backRef = useRef(null)
  useEffect(() => {
    backRef.current?.focus({ preventScroll: true })
  }, [])

  // Esc: back to the plan
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !e.defaultPrevented) closePbp()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const again = () => {
    setRi(0)
    resumePbp()
  }
  const next = () => (idx < slides.length - 1 ? setRi(idx + 1) : endPbp())
  const prev = () => setRi(Math.max(0, idx - 1))

  const beats = slides.length
    ? slides.map((s) => ({ id: s.id, label: s.kind === 'intro' ? 'Weak points' : s.kind === 'close' ? 'The answer' : pkgs[s.package - 1]?.name?.en || 'Package' }))
    : [
        { id: 'intro', label: 'Weak points' },
        { id: 'close', label: 'The answer' },
      ]

  return (
    <section className={`pbp${still ? ' is-still' : ''}${final ? ' is-final' : ''}`} aria-label="Watch it get built: the play-by-play">
      <div className="pbp__top">
        <button type="button" ref={backRef} className="pbp-back" onClick={closePbp} title="Back to the plan (Esc)">
          <span aria-hidden="true">←</span> Back to the plan
        </button>
        <ol className="pbp-steps" aria-label="The beats">
          {beats.map((b, i) => {
            const st = final || i < idx ? 'done' : i === idx ? 'now' : 'next'
            return (
              <li key={b.id} className={`pbp-steps__i is-${st}`} aria-current={st === 'now' ? 'step' : undefined}>
                <span className="pbp-steps__t">{b.label}</span>
                <span className="pbp-steps__bar" aria-hidden="true">
                  <i ref={st === 'now' ? nowBarRef : undefined} />
                </span>
              </li>
            )
          })}
        </ol>
        <Scoreboard m={m} shown={shown} goal={goal} />
      </div>

      {final ? (
        <Final answer={answer} script={script} onAgain={again} />
      ) : (
        <BeatCard key={beatId} slide={slide} pkgs={pkgs} K={K} cur={cur} problems={problems} m={m} script={script} loading={loading} error={error} />
      )}

      <div className="pbp__foot">
        {!final && (
          <div className="pbp-ctl">
            {still ? (
              <>
                <Button variant="secondary" onClick={prev} disabled={idx === 0 || !script}>
                  Previous
                </Button>
                <Button onClick={next} disabled={!script}>
                  {idx >= slides.length - 1 ? 'Show the answer' : 'Next'}
                </Button>
              </>
            ) : (
              <Button variant="secondary" onClick={u.capTalk ? holdPbp : resumePbp} disabled={!!error}>
                <span aria-hidden="true" className={`st-watch__icon${u.capTalk ? ' is-pause' : ''}`} />
                {u.capTalk ? 'Pause' : 'Play'}
              </Button>
            )}
            <WhoGrouped g={script?.grouping} />
          </div>
        )}
        {!final && (still ? <StillCaption slide={slide} n={slides.length} idx={idx} /> : <BuildCaption nb={nb} className="bn-cap--pbp" />)}
        <p className="pbp-syn">Synthetic grid model (Breakthrough Energy / Texas A&amp;M), not any utility’s network. Costs are the high end of estimates.</p>
      </div>
    </section>
  )
}

// ------------------------------------------------------------------ the scoreboard (top right)
function Scoreboard({ m, shown, goal }) {
  const cost = costAt(m, shown).high
  const cells = Math.max(goal, shown)
  return (
    <div className="pbp-score" role="group" aria-label="Scoreboard">
      <div className="pbp-score__block">
        <span className="pbp-score__k">Campuses at once</span>
        <span className="pbp-score__v">
          <strong key={shown} className="pbp-score__n">
            {shown}
          </strong>
          <span className="pbp-score__of">
            of {goal} · {m.today} today
          </span>
        </span>
        {cells > 0 && cells <= 48 && (
          <span className="pbp-score__meter" aria-hidden="true">
            {Array.from({ length: cells }, (_, i) => (
              <i key={i} className={i < m.today ? 'is-today' : i < shown ? 'is-built' : ''} />
            ))}
          </span>
        )}
      </div>
      <div className="pbp-score__block">
        <span className="pbp-score__k">Upgrades so far</span>
        <strong key={cost} className="pbp-score__money">
          {cost ? money(cost) : 'None yet'}
        </strong>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ the beat's card (left)
function BeatCard({ slide, pkgs, K, cur, problems, m, script, loading, error }) {
  let kicker
  let title
  let sub = null
  let stats = null
  if (error) {
    kicker = 'Watch it get built'
    title = 'The play-by-play didn’t load'
    sub = `${error?.message || 'The narration is unavailable'}. Go back to the plan, or try again in a moment.`
  } else if (loading) {
    kicker = 'Watch it get built'
    title = 'Preparing the play-by-play…'
    sub = 'Gemini groups the upgrades into packages and writes the lines; the engine checks every one.'
  } else if (!slide || slide.kind === 'intro') {
    const first = problems[0]
    const next = m.today + 1
    kicker = 'Before any upgrade'
    title = first && first.stops === next ? `What stops the ${ordinal(next)}` : `${m.today} fit at once today`
    if (first && first.stops === next) {
      sub = (
        <>
          Not the campus: <strong>{first.short}</strong>
          {first.base_pct != null ? `, already at ${first.base_pct} % of its rating on today’s grid` : ', a weak point already in today’s grid'}
          {problems.length > 1 ? `, and ${problems.length - 1} more weak ${problems.length - 1 === 1 ? 'point' : 'points'} like it.` : '.'}
        </>
      )
    }
    stats = [`${m.today} at once today`, K ? `${K} ${K === 1 ? 'package' : 'packages'} to build` : 'No upgrades in this budget']
  } else if (slide.kind === 'package') {
    const pk = pkgs[cur - 1]
    kicker = `Package ${cur} of ${K}`
    title = pk?.name?.en || slide.headline?.en
    sub = pk?.why?.en || null
    if (pk)
      stats = [
        `Up to ${money(pk.cost_high)}`,
        `${pk.upgrades} ${pk.upgrades === 1 ? 'upgrade' : 'upgrades'}: ${[pk.lines && `${pk.lines} ${pk.lines === 1 ? 'line' : 'lines'}`, pk.transformers && `${pk.transformers} ${pk.transformers === 1 ? 'transformer' : 'transformers'}`].filter(Boolean).join(', ')}`,
        campusesLabel(pk),
      ]
  } else {
    const n = script?.bought?.n ?? 0
    kicker = 'In all'
    title = `${n} at once, up from ${m.today}`
    sub = script?.bought?.cost_high ? `${money(script.bought.cost_high)} of upgrades, high end.` : null
    if (script?.plants?.binds) stats = [`Power plants cover ${fmt(script.plants.campuses)} with a ${fmt(script.plants.reserve_pct)} % reserve`, 'Past that: new generation or flexible hours']
  }
  return (
    <article className={`pbp-card pbp-card--${error ? 'error' : loading ? 'loading' : slide?.kind || 'intro'}`} aria-live="polite">
      <p className="pbp-card__kicker">{kicker}</p>
      <h2 className="pbp-card__title">{title}</h2>
      {sub && <p className="pbp-card__sub">{sub}</p>}
      {stats && (
        <ul className="pbp-card__stats">
          {stats.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      {error && (
        <Button variant="secondary" onClick={closePbp}>
          Back to the plan
        </Button>
      )}
    </article>
  )
}

// ------------------------------------------------------------------ the final frame
function Final({ answer, script, onAgain }) {
  return (
    <article className="pbp-final" aria-live="polite">
      <p className="pbp-card__kicker">The answer</p>
      <h2 className="pbp-final__main">{answer?.main}</h2>
      {answer?.sub && <p className="pbp-final__sub">{answer.sub}</p>}
      <div className="pbp-final__acts">
        <Button onClick={closePbp}>Back to the plan</Button>
        <Button variant="secondary" onClick={onAgain}>
          Watch again
        </Button>
      </div>
      <WhoGrouped g={script?.grouping} />
    </article>
  )
}

// ------------------------------------------------------------------ who grouped the packages
// The checks' fold is the agent's trace: what Gemini proposed, which checks it failed and why (the first draft and,
// after the one revision, the last), and what the engine's own grouping passed when it had to step in.
function CheckRows({ checks }) {
  return checks.map((c) => (
    <li key={c.id} className={c.ok ? 'is-ok' : 'is-bad'}>
      <span aria-hidden="true">{c.ok ? '✓' : '✗'}</span> {c.label}
      {!c.ok && c.detail && <em className="pbp-who__why">{c.detail}</em>}
    </li>
  ))
}

function WhoGrouped({ g }) {
  if (!g || !g.packages) return null
  const checks = g.checks || []
  const passed = checks.filter((c) => c.ok).length
  const gem = g.by !== 'gemini' && g.gemini_checks?.length ? g.gemini_checks : null
  const firstDraft = g.first_draft_rejections?.[0]
  const last = g.rejections?.[0]
  const list = gem ? (
    <details className="pbp-who__checks">
      <summary>
        Gemini’s grouping: {gem.filter((c) => c.ok).length} of {gem.length} checks passed
      </summary>
      <ul>
        <li className="pbp-who__h">Gemini’s grouping{g.rounds > 1 ? ', after one revision' : ''}</li>
        <CheckRows checks={gem} />
        {g.rounds > 1 && firstDraft && firstDraft !== last && <li className="pbp-who__note">First draft: {firstDraft}</li>}
        <li className="pbp-who__h">
          The engine’s grouping, used instead: {passed} of {checks.length} checks passed
        </li>
      </ul>
    </details>
  ) : (
    <details className="pbp-who__checks">
      <summary>
        {passed} of {checks.length} checks passed
      </summary>
      <ul>
        <CheckRows checks={checks} />
        {g.by === 'gemini' && g.revised && firstDraft && <li className="pbp-who__note">First draft sent back: {firstDraft}</li>}
      </ul>
    </details>
  )
  if (g.by === 'gemini')
    return (
      <div className="pbp-who">
        <AiBadge by="gemini" verified title="Gemini proposed the packages; the engine checked the grouping against the plan" />
        <span>
          Gemini grouped {g.units} paid {g.units === 1 ? 'step' : 'steps'} into {g.packages} {g.packages === 1 ? 'package' : 'packages'}
          {g.revised ? ' (revised once after a failed check)' : ''}.
        </span>
        {list}
      </div>
    )
  return (
    <div className="pbp-who">
      <AiBadge by="engine" />
      <span>
        grouped these: cut where the upgrades move across the state{g.reason ? ` (${WHY[g.reason] || g.reason})` : ''}.
      </span>
      {list}
    </div>
  )
}

// ------------------------------------------------------------------ reduced motion: the beat's words, all at once
function StillCaption({ slide, n, idx }) {
  if (!slide) return null
  const seg = slide.narration?.en?.[0]
  return (
    <div className={`bn-cap bn-cap--pbp bn-cap--${slide.kind} is-still`} role="region" aria-label="Narration">
      <div className="bn-cap__top">
        <span className="bn-cap__kicker">{slide.headline?.en}</span>
        <span className="bn-cap__count">
          {idx + 1} of {n}
        </span>
      </div>
      <p className="bn-cap__text" aria-live="polite">
        <span className="bn-w bn-w--said">{seg?.text}</span>
      </p>
    </div>
  )
}
