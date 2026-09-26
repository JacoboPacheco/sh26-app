import { useEffect, useMemo } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import AgentTrace, { AgentTraceToggle } from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { reportPeople } from '../cost/figures'
import { flexPin, keepsText, oftenText, pinsOf, rangeText, workText } from './beatMaps'
import { Kicker } from './ShowBits'
import {
  AI_BEAT,
  SOL,
  agenticOf,
  aiBeatMs,
  aiItemsOf,
  aiTraceOf,
  beatRows,
  greenAt,
  haveTo,
  mainOptions,
  moreOptions,
  mustRows,
  optionBeatMs,
  optionSay,
  usdCompact,
  usdShort,
} from './showDeck'
import { P, S, levelName } from './showText'
import { clamp01, easeInOut, useElapsed } from './useShowClock'

// the lines that overload before the fix: the trouble spots the fix has to clear
function troubleLines(report, result) {
  const fromResult = (result?.overloaded || []).map((x) => x.id).filter((x) => x != null)
  if (fromResult.length) return fromResult.slice(0, 40)
  const t = (report?.timeline || []).find((x) => x.lines?.length)
  return (t?.lines || []).map((l) => l.id)
}

// what the beat's heading says an option is: the lead (the cheapest way at full size; an operating rule with no new
// equipment; or, when nothing keeps the full campus, the closest way), the no-step-down alternative after an operating
// rule, another way
function roleOf(o, i, main, lang) {
  const T = P[lang]
  if (i === 0) {
    if (o.flex?.peak_only) return T.leadFlex
    if ((o.kept_pct ?? 0) < 99.5) return T.leadClosest
    return main.length > 1 ? T.leadRole : T.leadRoleOnly
  }
  if (main[0]?.family === 'flexible' && (o.family === 'upgrade' || o.family === 'agentic')) return T.altNoStep
  return T.altRole
}


// The map picture of one option: every element it upgrades drawn in green where it goes, each with its price pinned
// on (they land one by one, `step` apart, from `at`); an operating rule pins its step-down on the campus; a move shows
// the new site. `green`: the lines that were over their rating have cooled.
function optionLayer(O, o, { key, trouble, lang, at, step, green, still, present }) {
  const lines = [...trouble.map((id) => ({ id, tone: green ? 'cool' : 'over' }))]
  const ids = o.cost?.items?.length ? o.cost.items.map((it) => it.id) : o.lines.map((l) => l.id)
  ids.slice(0, 20).forEach((id, j) => lines.push({ id, tone: 'fix', delay: still ? 0 : at + Math.min(j, 5) * step }))
  const layer = { key, still, lines, tags: pinsOf(O, o, lang, { delay: still ? 0 : at, step: still ? 0 : step }), marks: [] }
  const site = o.site || (o.apply?.lat != null ? { lat: o.apply.lat, lon: o.apply.lon } : null)
  if (o.family === 'move' && site) layer.ghost = site
  if (o.family === 'flexible' && o.flex && present?.site) {
    const here = [present.site.lon, present.site.lat]
    layer.marks.push({ at: here, tone: 'fix', r: 16 })
    layer.tags.push({ at: here, ...flexPin(o.flex, lang), tone: 'fix', delay: still ? 0 : at })
  }
  return layer
}

// The solutions, the proportionate way (PRESENT V2): the cheapest verified way to keep the campus at full size first,
// placed on the map where it goes, each element's price pinned on it and the total adding up as they land; the re-run
// counts the people out down to zero; then the price is weighed against the blackout it prevents and how often the
// overload happens. Pricier full-size plans come after ("if you want no step-downs"); a smaller campus, another site
// or on-site generation only under "More options". Then, when the AI proposer has run, "Watch the AI work".
export default function ShowSolutions({ slide, report, lang, animate, options, stage, live, agentic }) {
  const t = S[lang]
  const T = P[lang]
  const O = useOverload()
  const main = useMemo(() => mainOptions(options), [options])
  const more = useMemo(() => moreOptions(options), [options])
  const n = main.length
  const beats = useMemo(() => main.map((x) => optionBeatMs(x, lang)), [main, lang])
  const ag = useMemo(() => aiTraceOf(slide, { agentic }), [slide, agentic])
  const run = useMemo(() => agenticOf(slide, { agentic }), [slide, agentic]) // the paused view shows any finished run, even one where Gemini didn't answer
  const aiN = useMemo(() => aiItemsOf(ag).length, [ag])
  const optionsMs = SOL.intro + beats.reduce((a, b) => a + b, 0)
  const total = optionsMs + aiBeatMs(ag)
  // with a voice the options follow its cues, so the clock keeps running long enough for the AI beat after them
  const clock = useElapsed(animate, total + (live.optionCues ? 90000 : 4000))
  const trouble = useMemo(() => troubleLines(report, O.result), [report, O.result])
  // the re-run counts down from the results panel's headline figure (people hit) to what the fix leaves
  const before = reportPeople(report).hit
  const headline = haveTo(report, lang)
  const present = useMemo(
    () => ({ ...(slide.present || {}), site: report?.case?.sub_lon != null ? { lat: report.case.sub_lat, lon: report.case.sub_lon } : null }),
    [slide.present, report],
  )
  const blackout = present.blackout
  const often = oftenText(present.often, lang)

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
    stage.say({ key: 'ai-work', text: t.aiWorkSay(ag.asked || 0, ag.verified || 0, ag.rounds || 1) }) // "sent back what still failed" only when it did
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiOn, lang])

  const opt = cur >= 0 ? main[cur] : null
  const tg = opt ? greenAt(opt, lang) : 0
  const green = !!opt && local >= tg

  // the map: the lines past their rating (red), then this option's elements land one by one where they go, each with
  // its price; at the reveal the red lines cool to green. The camera frames what the option touches and the campus.
  const camKey = opt ? `${cur}` : ''
  useEffect(() => {
    if (!animate) return
    if (!opt) {
      stage.layer({ key: 'intro', lines: trouble.map((id) => ({ id, tone: 'over' })) })
      stage.say({ key: 'opt-intro', text: `${headline}. ${S[lang].frame}` })
      stage.camera({ line_ids: trouble, points: present.site ? [[present.site.lon, present.site.lat]] : [] })
      return
    }
    const ids = opt.cost?.items?.length ? opt.cost.items.map((it) => it.id) : opt.lines.map((l) => l.id)
    const points = []
    if (present.site) points.push([present.site.lon, present.site.lat])
    const site = opt.site || (opt.apply?.lat != null ? { lat: opt.apply.lat, lon: opt.apply.lon } : null)
    if (opt.family === 'move' && site) points.push([site.lon, site.lat])
    stage.camera({ line_ids: opt.family === 'move' || opt.family === 'flexible' ? [] : ids.length ? ids : trouble, points })
    stage.layer(optionLayer(O, opt, { key: `opt-${cur}`, trouble, lang, at: SOL.lineAt, step: SOL.lineStep, green: false, still: false, present }))
    stage.say({ key: `opt-${cur}`, text: optionSay(opt, cur, n, lang) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate, camKey, lang])
  useEffect(() => {
    if (!animate || !opt || !green) return
    // the same layer key: what already landed stays; only the red lines cool
    stage.layer(optionLayer(O, opt, { key: `opt-${cur}`, trouble, lang, at: SOL.lineAt, step: SOL.lineStep, green: true, still: false, present }))
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
  // the finished picture (paused, or reduced motion): the lead is in where it goes, its prices pinned, the trouble cooled
  const top = main[0]
  useEffect(() => {
    if (animate || !top) return
    stage.layer(optionLayer(O, top, { key: 'still', trouble, lang, at: 0, step: 0, green: true, still: true, present }))
    const ids = top.cost?.items?.length ? top.cost.items.map((it) => it.id) : top.lines.map((l) => l.id)
    stage.camera({ line_ids: top.family === 'flexible' || top.family === 'move' ? [] : ids, points: present.site ? [[present.site.lon, present.site.lat]] : [] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate, top, trouble, stage, lang])

  if (!animate) {
    // a paused slide is the whole comparison at once
    return (
      <>
        <div className="sh-headrow">
          <Kicker tone="green">{t.solutions}</Kicker>
          <span className="sh-count">{n + more.length}</span>
        </div>
        <h2 className="rs-headline" id={`rs-h-${slide.id}`}>
          {headline}
        </h2>
        <ol className="sh-plan">
          {main.map((o, i) => (
            <PlanRow key={i} o={o} i={i} main={main} lang={lang} />
          ))}
        </ol>
        {top && <Weigh opt={top} blackout={blackout} often={often} lang={lang} animate={false} />}
        <MoreOptions more={more} lang={lang} />
        <Frame agentic={run || agentic} options={main} lang={lang} />
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
  const rows = opt ? beatRows(opt, lang) : 0
  const shownRows = opt ? Math.min(rows, Math.max(0, Math.floor((local - SOL.lineAt) / SOL.lineStep) + 1)) : 0
  const beat = opt ? beats[cur] : 1
  const ai = opt?.by === 'gemini'

  return (
    <>
      <div className="sh-headrow">
        <Kicker tone="green">{t.solutions}</Kicker>
        <span className="sh-count">{opt ? `${t.option} ${cur + 1} ${t.of} ${n}` : `${n} ${t.solutions.toLowerCase()}`}</span>
      </div>
      {n > 1 && (
        <ol className="sh-rail" aria-hidden="true">
          {main.map((x, i) => (
            <li key={i} className={i === cur ? 'sh-rail__on' : i < cur ? 'sh-rail__done' : ''} style={{ '--p': i === cur ? clamp01(local / beat) : i < cur ? 1 : 0 }}>
              <span>
                {t.rank(i + 1)}
                {x.by === 'gemini' && <em className="sh-ai">AI</em>}
              </span>
              <b>{x.cost?.high ? usdShort(x.cost.high) : x.family === 'flexible' ? '$0' : `${Math.round(x.kept_pct)}%`}</b>
            </li>
          ))}
        </ol>
      )}

      {!opt && (
        <>
          <h2 className="rs-headline sh-have" id={`rs-h-${slide.id}`}>
            {headline}
          </h2>
          <ul className="sh-overview">
            {main.map((x, i) => (
              <li key={i} style={{ '--i': i }}>
                {x.name[lang]}
                {x.by === 'gemini' && <em className="sh-ai">AI</em>}
              </li>
            ))}
          </ul>
          <Frame agentic={agentic} options={main} lang={lang} />
        </>
      )}

      {opt && (
        <div className="sh-opt" key={cur}>
          <p className="sh-opt__have">{roleOf(opt, cur, main, lang)}</p>
          <h2 className="sh-opt__name" id={`rs-h-${slide.id}`}>
            {ai && <em className="sh-ai sh-ai--big">{t.aiPlan}</em>}
            {opt.name[lang]}
          </h2>
          {opt.sub?.[lang] && <p className="sh-opt__sub">{opt.sub[lang]}</p>}
          <OptionDetail o={opt} shown={shownRows} lang={lang} />

          <div className={`sh-rerun${local >= tg - 250 ? ' sh-rerun--go' : ''}${runDone ? ' sh-rerun--done' : ''}`} style={{ '--g': g }}>
            <p className="sh-rerun__label">{runDone ? t.peopleOut : local >= tg - 250 ? t.rerun : t.scPeople}</p>
            <p className="sh-rerun__n" aria-hidden="true">
              {fmt(people)}
            </p>
            <span className="sh-sr">{`${t.peopleOut}: ${fmt(after)}`}</span>
            {runDone && <p className="sh-rerun__zero">{after === 0 ? t.zeroOut : opt.verdict === 'partly' ? t.partly : t.peopleOutN(fmt(after))}</p>}
            <span className="sh-rerun__scan" aria-hidden="true" />
          </div>

          {revealed && (
            <>
              <Weigh opt={opt} blackout={blackout} often={cur === 0 ? often : null} lang={lang} animate />
              <p className="sh-verified">
                <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                  <path d="M3 8.5 6.5 12 13 4.5" fill="none" stroke="currentColor" strokeWidth="2" pathLength="1" />
                </svg>
                {ai ? t.verifiedAI : t.verifiedEngine}
                {` · ${keepsText(opt, T)}`}
              </p>
            </>
          )}
          {cur === n - 1 && revealed && more.length > 0 && <MoreOptions more={more} lang={lang} compact />}
        </div>
      )}
    </>
  )
}

// what an option consists of, one row at a time: each priced element (the pin's words and price), the operating rule's
// sizes per load level, or the "you have to" list
function OptionDetail({ o, shown, lang }) {
  const T = P[lang]
  const items = o.cost?.items || []
  if (items.length) {
    const on = items.slice(0, Math.min(shown, 6))
    const lo = on.reduce((a, it) => a + (Number(it.low) || 0), 0)
    const hi = on.reduce((a, it) => a + (Number(it.high) || 0), 0)
    return (
      <div className="sh-items">
        <p className="sh-items__head">{T.perElement}</p>
        <ul>
          {on.map((it) => (
            <li key={it.id} className="sh-items__row">
              <b>{rangeText(it.low, it.high)}</b>
              <span>{workText(it, lang)}</span>
            </li>
          ))}
        </ul>
        <p className="sh-items__total">
          {T.total}: <b>{on.length ? rangeText(lo, hi) : '–'}</b>
          {items.length > 6 && on.length >= 6 && <span> · +{items.length - 6}</span>}
        </p>
      </div>
    )
  }
  if (o.flex?.levels?.length) return <FlexStrip flex={o.flex} total={o.from_mw} shown={shown} lang={lang} />
  const list = mustRows(o, lang)
  return (
    <ul className="sh-must">
      {list.slice(0, shown).map((line, i) => (
        <li key={i} className="sh-must__row">
          {line}
        </li>
      ))}
    </ul>
  )
}

// the operating rule: what the campus can run at each load level the engine checked (full size marked), no new
// equipment; its cost is compute, under a sourced, labeled assumption
function FlexStrip({ flex, total, shown, lang }) {
  const T = P[lang]
  const levels = flex.levels
  const tot = Number(total) || Math.max(...levels.map((x) => x.runs_mw), 1)
  return (
    <div className="sh-flex">
      <p className="sh-items__head">{T.flexTitle}</p>
      <ol className="sh-flex__bars">
        {levels.map((x, i) => (
          <li key={x.level} className={`${x.full ? 'sh-flex--full' : ''}${i < shown ? ' sh-flex--on' : ''}`} style={{ '--w': `${Math.min(100, (x.runs_mw / tot) * 100)}%` }}>
            <span className="sh-flex__lvl">{levelName(x.name, lang)}</span>
            <span className="sh-flex__bar" aria-hidden="true">
              <i />
            </span>
            <b>{x.full ? T.flexFull : `${fmt(x.runs_mw)} MW`}</b>
          </li>
        ))}
      </ol>
      <p className="sh-flex__cost">
        <b>{T.noEquipment}.</b> {T.flexCompute(flex.mwh_year, flex.energy_share_pct, flex.hours_assumed)}
      </p>
      <p className="sh-flex__assume">{T.flexAssume(flex.hours_assumed)}</p>
    </div>
  )
}

// the price next to what it prevents, on one scale: the blackout's estimate (red, the full width) and this fix (green);
// how often the overload happens; that the chain reaction assumes no operator acts
function Weigh({ opt, blackout, often, lang, animate }) {
  const T = P[lang]
  const bHi = Number(blackout?.high) || 0
  const bLo = Number(blackout?.low) || 0
  const fHi = Number(opt?.cost?.high) || 0
  const fLo = Number(opt?.cost?.low) || 0
  if (!bHi) return often ? <p className="sh-weigh__often">{often}</p> : null
  const pct = fHi ? Math.max(0.6, Math.min(100, (fHi / bHi) * 100)) : 0
  return (
    <section className={`sh-weigh${animate ? ' sh-weigh--anim' : ''}`} aria-label={T.weighTitle}>
      <p className="sh-items__head">{T.weighTitle}</p>
      <div className="sh-weigh__row sh-weigh__row--lost" style={{ '--w': '100%' }}>
        <span>{T.blackoutBar}</span>
        <i aria-hidden="true" />
        <b>{rangeText(bLo, bHi)}</b>
      </div>
      <div className="sh-weigh__row sh-weigh__row--fix" style={{ '--w': `${pct}%` }}>
        <span>{T.fixBar}</span>
        <i aria-hidden="true" />
        <b>{fHi ? rangeText(fLo, fHi) : opt?.family === 'flexible' ? T.noEquipment : S[lang].costNone}</b>
      </div>
      {often && <p className="sh-weigh__often">{often}</p>}
      <p className="sh-weigh__note">{T.protection}</p>
    </section>
  )
}

// one main option in the paused comparison: its role, name, price, what it keeps
function PlanRow({ o, i, main, lang }) {
  const t = S[lang]
  const T = P[lang]
  return (
    <li className={`sh-planrow${i === 0 ? ' sh-planrow--lead' : ''}`}>
      <p className="sh-planrow__role">
        {roleOf(o, i, main, lang)}
        {o.by === 'gemini' && <em className="sh-ai">{t.aiPlan}</em>}
      </p>
      <p className="sh-planrow__name">{o.name[lang]}</p>
      <p className="sh-planrow__meta">
        <b>{o.cost?.high ? rangeText(o.cost.low, o.cost.high) : o.family === 'flexible' ? T.noEquipment : t.costNone}</b>
        <span>{keepsText(o, T)}</span>
        <span className={Number(o.outcome?.people) ? '' : 'sh-ok'}>{Number(o.outcome?.people) ? t.peopleOutN(fmt(o.outcome.people)) : t.zeroOut}</span>
      </p>
    </li>
  )
}

// the rest of the verified options, folded: they build less here, or elsewhere; never the answer the show leads with
function MoreOptions({ more, lang, compact = false }) {
  const T = P[lang]
  const t = S[lang]
  if (!more.length) return null
  // what the folded ones are: pricier full-size upgrade plans, and/or ways that build less here or elsewhere
  const up = more.some((o) => (o.family === 'upgrade' || o.family === 'agentic') && (o.kept_pct ?? 0) >= 99.5)
  const less = more.some((o) => !((o.family === 'upgrade' || o.family === 'agentic') && (o.kept_pct ?? 0) >= 99.5))
  return (
    <details className={`sh-more${compact ? ' sh-more--compact' : ''}`}>
      <summary>
        {T.moreTitle} <span>({more.length})</span>
      </summary>
      <p className="sh-more__note">{up && less ? T.moreNoteBoth : up ? T.moreNoteUp : T.moreNoteLess}</p>
      <ul>
        {more.map((o, i) => (
          <li key={i}>
            <span>{o.name[lang]}</span>
            <b>{o.cost?.high ? usdShort(o.cost.high) : keepsText(o, T)}</b>
            {o.by === 'gemini' && <em className="sh-ai">{t.aiPlan}</em>}
          </li>
        ))}
      </ul>
    </details>
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
        <b>{high ? usdShort(high) : '–'}</b>
      </div>
      <span className="sh-sr">{`${fmt(o.kept_mw)} MW, ${Math.round(o.kept_pct)}% ${t.kept}; ${high ? t.costHigh(usdCompact(high, lang)) : t.costNone}`}</span>
    </li>
  )
}

export { Weigh, MoreOptions }
