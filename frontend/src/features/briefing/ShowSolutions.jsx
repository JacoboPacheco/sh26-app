import { useEffect, useMemo } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import AgentTrace, { AgentTraceToggle } from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { Kicker } from './ShowBits'
import { AI_BEAT, SOL, agenticOf, aiBeatMs, aiItemsOf, aiTraceOf, greenAt, haveTo, mustRows, optionBeatMs, optionSay, usdCompact } from './showDeck'
import { S } from './showText'
import { clamp01, easeInOut, useElapsed } from './useShowClock'

// the lines that overload before the fix: the trouble spots the fix has to clear
function troubleLines(report, result) {
  const fromResult = (result?.overloaded || []).map((x) => x.id).filter((x) => x != null)
  if (fromResult.length) return fromResult.slice(0, 40)
  const t = (report?.timeline || []).find((x) => x.lines?.length)
  return (t?.lines || []).map((l) => l.id)
}

// The solutions, one beat per option: "if you want to build this here, you have to do this" (line by line),
// then the green reveal: the lines that broke cool from red to green, the upgrades draw in on the map, the
// re-run counts the people out down to zero, and the cost (high end) and the "verified" tag land. Then, when the
// AI proposer has run, "Watch the AI work": a few of its steps (a plan that failed, the engine's findings going
// back, the revision that held), each one the engine's real verdict.
export default function ShowSolutions({ slide, report, lang, animate, options, stage, live, agentic }) {
  const t = S[lang]
  const o = useOverload()
  const n = options.length
  const beats = useMemo(() => options.map((x) => optionBeatMs(x, lang)), [options, lang])
  const ag = useMemo(() => aiTraceOf(slide, { agentic }), [slide, agentic])
  const run = useMemo(() => agenticOf(slide, { agentic }), [slide, agentic]) // the paused view shows any finished run, even one where Gemini didn't answer
  const aiN = useMemo(() => aiItemsOf(ag).length, [ag])
  const optionsMs = SOL.intro + beats.reduce((a, b) => a + b, 0)
  const total = optionsMs + aiBeatMs(ag)
  // with a voice the options follow its cues, so the clock keeps running long enough for the AI beat after them
  const clock = useElapsed(animate, total + (live.optionCues ? 90000 : 4000))
  const trouble = useMemo(() => troubleLines(report, o.result), [report, o.result])
  const before = Number(report?.event?.people) || 0
  const headline = haveTo(report, lang)

  // which option is on: the narration's option cues when it has them, else the show's own clock
  let cur = -1
  let local = 0
  if (animate) {
    if (live.optionCues) {
      if (live.option) {
        cur = Math.min(n - 1, Math.max(0, live.option.n))
        local = performance.now() - live.option.at
      }
    } else {
      let t0 = SOL.intro
      if (clock >= t0) {
        cur = n - 1
        local = beats[n - 1]
        for (let i = 0; i < n; i++) {
          if (clock < t0 + beats[i]) {
            cur = i
            local = clock - t0
            break
          }
          t0 += beats[i]
        }
      }
    }
  }
  // the AI beat: after the last option's beat has run out
  let aiLocal = -1
  if (animate && aiN > 0) {
    if (live.optionCues) {
      if (live.option && cur === n - 1 && local >= beats[n - 1]) aiLocal = local - beats[n - 1]
    } else if (clock >= optionsMs) aiLocal = clock - optionsMs
  }
  const aiOn = aiLocal >= 0
  const aiShown = aiOn ? Math.max(0, Math.min(aiN, Math.floor((aiLocal - AI_BEAT.intro) / AI_BEAT.step) + 1)) : 0
  useEffect(() => {
    if (!aiOn || !ag) return
    stage.say({ key: 'ai-work', text: t.aiWorkSay(ag.asked || 0, ag.verified || 0, ag.rounds || 1) })  // "sent back what still failed" only when it did
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiOn, lang])

  const opt = cur >= 0 ? options[cur] : null
  const tg = opt ? greenAt(opt, lang) : 0
  const green = !!opt && local >= tg

  // the map: the lines that broke (red) while the plan is read out, then they cool to green and the plan's
  // upgrades draw in; a move shows the new site. The camera flies to what the option touches.
  const camKey = opt ? `${cur}` : ''
  useEffect(() => {
    if (!animate) return
    if (!opt) {
      stage.layer({ key: 'intro', lines: trouble.map((id) => ({ id, tone: 'over' })), ghost: null })
      stage.say({ key: 'opt-intro', text: `${headline}. ${S[lang].frame}` })
      return
    }
    const line_ids = opt.lines.length ? opt.lines.map((l) => l.id) : trouble
    const points = []
    if (opt.family === 'move' && opt.site) points.push([opt.site.lon, opt.site.lat])
    if (opt.family === 'move' && opt.apply?.lat != null) points.push([opt.apply.lon, opt.apply.lat])
    if (opt.family === 'move' || !line_ids.length) points.push([report?.case?.sub_lon, report?.case?.sub_lat])
    stage.camera({ line_ids: opt.family === 'move' ? [] : line_ids, points: points.filter((p) => p[0] != null) })
    stage.layer({ key: `${camKey}-a`, lines: trouble.map((id) => ({ id, tone: 'over' })), ghost: null })
    stage.say({ key: `opt-${cur}`, text: optionSay(opt, cur, n, lang) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate, camKey, lang])
  useEffect(() => {
    if (!animate || !opt || !green) return
    const lines = [...trouble.map((id) => ({ id, tone: 'cool' })), ...opt.lines.map((l) => ({ id: l.id, tone: 'fix' }))]
    const site = opt.family === 'move' ? opt.site || (opt.apply?.lat != null ? { lat: opt.apply.lat, lon: opt.apply.lon } : null) : null
    stage.layer({ key: `${camKey}-b`, lines, ghost: site })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate, camKey, green])
  // the result is said once the counter has run down (the reveal is the moment)
  const revealed = !!opt && local >= tg + SOL.run
  useEffect(() => {
    if (!animate || !opt || !revealed) return
    stage.say({ key: `opt-${cur}-done`, text: optionSay(opt, cur, n, lang, true) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate, camKey, revealed, lang])
  useEffect(
    () => () => {
      stage.layer(null)
      stage.say(null)
    },
    [stage],
  )
  // the finished picture (paused, or reduced motion): the top pick is in, the trouble lines are green
  const top = options[0]
  useEffect(() => {
    if (animate || !top) return
    stage.layer({ key: 'still', lines: [...trouble.map((id) => ({ id, tone: 'cool' })), ...top.lines.map((l) => ({ id: l.id, tone: 'fix' }))], ghost: null })
  }, [animate, top, trouble, stage])

  if (!animate) {
    // a paused slide is the whole comparison at once
    return (
      <>
        <div className="sh-headrow">
          <Kicker tone="green">{t.solutions}</Kicker>
          <span className="sh-count">{n}</span>
        </div>
        <h2 className="rs-headline" id={`rs-h-${slide.id}`}>
          {headline}
        </h2>
        <OptionRows options={options} lang={lang} animate={false} />
        <Frame agentic={run || agentic} options={options} lang={lang} />
        {run && (
          <AgentTraceToggle
            trace={run.trace}
            lang={lang}
            label={run.asked > 0 ? `${t.aiWork} · ${t.aiWorkCount(run.asked, run.verified || 0, run.rounds || 1)}` : t.aiWork}
            heading={false}
            stepMs={420}
            follow
            className="sh-aitrace"
          />
        )}
      </>
    )
  }

  if (aiOn) {
    return (
      <div className="sh-aiwork">
        <div className="sh-headrow">
          <Kicker tone="green">{t.aiWork}</Kicker>
          <span className="sh-count">{t.aiWorkCount(ag.asked || 0, ag.verified || 0, ag.rounds || 1)}</span>
        </div>
        <h2 className="rs-headline sh-aiwork__h" id={`rs-h-${slide.id}`}>
          {(ag.rounds || 1) > 1 ? t.aiWorkHead : t.aiWorkHead1}
        </h2>
        <AgentTrace trace={ag.trace} lang={lang} shown={aiShown} max={AI_BEAT.items} heading={false} stepMs={AI_BEAT.step} follow brief className="sh-aitrace" />
        <p className="sh-aiwork__foot">
          <AiBadge by="gemini" verified lang={lang} />
        </p>
      </div>
    )
  }

  const g = opt ? easeInOut(clamp01((local - tg) / SOL.run)) : 0
  const after = opt ? Number(opt.outcome?.people) || 0 : 0
  const people = before + (after - before) * g
  const runDone = g >= 1
  const list = opt ? mustRows(opt, lang) : []
  const shownRows = opt ? Math.min(list.length, Math.max(0, Math.floor((local - SOL.lineAt) / SOL.lineStep) + 1)) : 0
  const beat = opt ? beats[cur] : 1
  const ai = opt?.by === 'gemini'

  return (
    <>
      <div className="sh-headrow">
        <Kicker tone="green">{t.solutions}</Kicker>
        <span className="sh-count">{opt ? `${t.option} ${cur + 1} ${t.of} ${n}` : `${n} ${t.solutions.toLowerCase()}`}</span>
      </div>
      <ol className="sh-rail" aria-hidden="true">
        {options.map((x, i) => (
          <li key={i} className={i === cur ? 'sh-rail__on' : i < cur ? 'sh-rail__done' : ''} style={{ '--p': i === cur ? clamp01(local / beat) : i < cur ? 1 : 0 }}>
            <span>
              {t.rank(i + 1)}
              {x.by === 'gemini' && <em className="sh-ai">AI</em>}
            </span>
            <b>{Math.round(x.kept_pct)}%</b>
          </li>
        ))}
      </ol>

      {!opt && (
        <>
          <h2 className="rs-headline sh-have" id={`rs-h-${slide.id}`}>
            {headline}
          </h2>
          <ul className="sh-overview">
            {options.map((x, i) => (
              <li key={i} style={{ '--i': i }}>
                {x.name[lang]}
                {x.by === 'gemini' && <em className="sh-ai">AI</em>}
              </li>
            ))}
          </ul>
          <Frame agentic={agentic} options={options} lang={lang} />
        </>
      )}

      {opt && (
        <div className="sh-opt" key={cur}>
          <p className="sh-opt__have">{headline}</p>
          <h2 className="sh-opt__name" id={`rs-h-${slide.id}`}>
            {ai && <em className="sh-ai sh-ai--big">{t.aiPlan}</em>}
            {opt.name[lang]}
          </h2>
          {opt.sub?.[lang] && <p className="sh-opt__sub">{opt.sub[lang]}</p>}
          <ul className="sh-must">
            {list.slice(0, shownRows).map((line, i) => (
              <li key={i} className="sh-must__row">
                {line}
              </li>
            ))}
          </ul>

          <div className={`sh-rerun${local >= tg - 250 ? ' sh-rerun--go' : ''}${runDone ? ' sh-rerun--done' : ''}`} style={{ '--g': g }}>
            <p className="sh-rerun__label">{local >= tg - 250 && !runDone ? t.rerun : t.peopleOut}</p>
            <p className="sh-rerun__n" aria-hidden="true">
              {fmt(people)}
            </p>
            <span className="sh-sr">{`${t.peopleOut}: ${fmt(after)}`}</span>
            {runDone && <p className="sh-rerun__zero">{after === 0 ? t.zeroOut : opt.verdict === 'partly' ? t.partly : t.peopleOutN(fmt(after))}</p>}
            <span className="sh-rerun__scan" aria-hidden="true" />
          </div>

          {revealed && (
            <div className="sh-result">
              <div className="sh-kept" style={{ '--kept': `${opt.kept_pct}%` }}>
                <span>
                  {t.keeps}: <b>{fmt(opt.kept_mw)} MW</b> · {Math.round(opt.kept_pct)}%
                </span>
                <i />
              </div>
              <p className="sh-cost">
                {opt.cost?.high ? (
                  <>
                    {t.costHigh(usdCompact(opt.cost.high))} <span>({t.costHighNote})</span>
                  </>
                ) : (
                  t.costNone
                )}
              </p>
              <p className="sh-verified">
                <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                  <path d="M3 8.5 6.5 12 13 4.5" fill="none" stroke="currentColor" strokeWidth="2" pathLength="1" />
                </svg>
                {ai ? t.verifiedAI : t.verifiedEngine}
              </p>
            </div>
          )}
        </div>
      )}
    </>
  )
}

// "AI finds the fixes, the physics engine verifies every one" with what the proposer did for this case
export function Frame({ agentic, options, lang }) {
  const t = S[lang]
  const held = options.filter((x) => x.verdict === 'holds').length
  let line = t.engineFound(held)
  if (agentic?.status === 'running') line = t.aiThinking
  else if (agentic?.status === 'done' && agentic.asked > 0) line = t.aiFound(agentic.asked, agentic.verified ?? agentic.added ?? 0)
  return (
    <p className={`sh-frame${agentic?.status === 'running' ? ' sh-frame--busy' : ''}`}>
      <b>{t.frame}</b> {line}
    </p>
  )
}

// the options side by side: how much of the campus each keeps and what it costs (high end); bars draw in
export function OptionRows({ options, lang, animate, delay = 0 }) {
  const maxCost = Math.max(1, ...options.map((o) => Number(o.cost?.high) || 0))
  return (
    <ol className="sh-orows">
      {options.map((o, i) => (
        <OptionRow key={i} o={o} lang={lang} best={i === 0} delay={delay + i * 550} animate={animate} maxCost={maxCost} />
      ))}
    </ol>
  )
}

function OptionRow({ o, lang, best, delay, animate, maxCost }) {
  const t = S[lang]
  const after = Number(o.outcome?.people) || 0
  const high = Number(o.cost?.high) || 0
  return (
    <li
      className={`sh-orow${best ? ' sh-orow--best' : ''}${animate ? ' sh-orow--anim' : ''}`}
      style={{ '--d': `${delay}ms`, '--kept': `${Math.min(100, o.kept_pct)}%`, '--cost': `${high ? Math.max(3, (high / maxCost) * 100) : 0}%` }}
    >
      <p className="sh-orow__top">
        <span className="sh-orow__k">{t.rank(o.k + 1)}</span>
        {best && <span className="sh-orow__best">{t.bestPick}</span>}
        {o.by === 'gemini' && <em className="sh-ai">{t.aiPlan}</em>}
        <span className={`sh-orow__out${after === 0 ? ' sh-ok' : ''}`}>{after === 0 ? t.zeroOut : t.peopleOutN(fmt(after))}</span>
      </p>
      <p className="sh-orow__name">{o.name[lang]}</p>
      <div className="sh-orow__bars" aria-hidden="true">
        <span className="sh-orow__lbl">{t.keptBar}</span>
        <span className="sh-orow__bar">
          <i />
        </span>
        <b>{Math.round(o.kept_pct)}%</b>
        <span className="sh-orow__lbl">{t.costBar}</span>
        <span className="sh-orow__bar sh-orow__bar--cost">
          <i />
        </span>
        <b>{high ? usdCompact(high) : '–'}</b>
      </div>
      <span className="sh-sr">{`${fmt(o.kept_mw)} MW, ${Math.round(o.kept_pct)}% ${t.kept}; ${high ? t.costHigh(usdCompact(high)) : t.costNone}`}</span>
    </li>
  )
}
