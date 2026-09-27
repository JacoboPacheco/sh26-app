import { useEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Gauge, Kicker } from './ShowBits'
import { playsOf, restOf, restParts, restSay, scoreAt } from './showDeck'
import { S } from './showText'
import { useTween } from './useShowClock'

const SNAP_MS = 350 // where a play's failure lands inside its tier (shell/cascadeSchedule.js)
const PLAY_GAP_MS = 1500 // without the map's replay clock: one play every so often

// When play `p` lands, in ms from the replay's start: the map's schedule when it runs, else evenly spaced.
function landAt(fx, p, i, count) {
  const sch = fx?.schedule
  if (!sch?.tiers?.length) return null
  const tier = sch.tiers.find((x) => x.n === p.n)
  if (tier) return tier.t0 + SNAP_MS * (sch.scale || 1)
  return ((i + 0.6) / Math.max(1, count)) * sch.total
}

// The chain as a broadcast. PLAYS, SUMMARIZED (user, Sat 20:29): the map replays every step and the step counter and the
// scoreboard keep the totals as each lands, but only the BIG plays (the first failure, the ones that hit the most people
// or split the grid: about three) get a card, a banner and a caption, each with its line and its toll; once the last
// step has landed, ONE card sums up the rest ("6 more lines tripped, hitting another 47,565 people"). The plays land on
// the map's own clock (its replay schedule) or on the narration's step cues, whichever is ahead, so the cards and the
// map never disagree.
export default function ShowChain({ slide, report, lang, animate, stage, cueStep }) {
  const t = S[lang]
  const o = useOverload()
  const cascade = o.cascade
  const plays = useMemo(() => playsOf(report, slide, cascade), [report, slide, cascade])
  const rest = useMemo(() => restOf(slide, plays), [slide, plays])
  const [shown, setShown] = useState(animate ? 0 : plays.length)
  const fxRef = useRef(null)
  const cueRef = useRef(0)
  useEffect(() => {
    fxRef.current = o.fx
    cueRef.current = cueStep || 0
  })
  const headline = slide.headline?.[lang] || slide.headline?.en || ''

  // which plays have landed (every step: the map, the step counter and the scoreboard follow them all)
  useEffect(() => {
    if (!animate) return undefined
    const t0 = performance.now()
    let raf = 0
    const tick = (now) => {
      const fx = fxRef.current
      let n = 0
      if (fx?.schedule?.tiers?.length) {
        const el = now - fx.startedAt
        n = plays.filter((p, i) => (landAt(fx, p, i, plays.length) ?? Infinity) <= el).length
      } else n = Math.max(0, Math.floor((now - t0 - 900) / PLAY_GAP_MS) + 1)
      const viaCue = plays.filter((p) => p.n <= cueRef.current).length
      n = Math.min(plays.length, Math.max(n, viaCue))
      setShown((s) => (s >= n ? s : n))
      if (n < plays.length) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [animate, plays])

  // the scoreboard follows every step; the banner and the caption only the big plays, then the rest in one line
  const score = scoreAt(plays, shown)
  const landed = plays.slice(0, shown)
  const bigLanded = landed.filter((p) => p.big)
  const lastBig = bigLanded[bigLanded.length - 1] || null
  const allIn = shown >= plays.length
  useEffect(() => {
    stage.setPlay(shown)
  }, [shown, stage])
  useEffect(() => {
    if (!animate || !lastBig) return
    stage.callout(calloutFor(lastBig, t, lang, lastBig === plays.find((p) => p.big)))
    stage.say({ key: `play-${lastBig.n}`, text: lastBig.say[lang] }) // shown whenever the narration isn't speaking
  }, [lastBig, animate, plays, lang, stage, t])
  useEffect(() => {
    if (!animate || !allIn || !rest) return
    const text = restSay(rest, lang)
    stage.callout({ key: 'rest', tone: 'amber', head: t.restHead, text: text.replace(/^(Beyond those|Además), /, '').replace(/\.$/, ''), sub: '' })
    stage.say({ key: 'play-rest', text })
  }, [allIn, rest, animate, lang, stage, t])
  useEffect(
    () => () => {
      stage.callout(null)
      stage.say(null)
    },
    [stage],
  )

  const newest = bigLanded.length - 1
  return (
    <>
      <div className="sh-headrow">
        <Kicker tone="red">{t.playByPlay}</Kicker>
        <span className="sh-count">
          {t.step} {Math.max(shown, animate ? 0 : plays.length)} {t.of} {plays.length}
        </span>
      </div>
      <div className="sh-drive" aria-hidden="true">
        {plays.map((p, i) => (
          <i key={p.n} className={`${i < shown ? (i === shown - 1 && animate ? 'on new' : 'on') : ''}${p.big ? ' big' : ''}`} />
        ))}
      </div>
      <h2 className="rs-headline sh-headline--small" id={`rs-h-${slide.id}`}>
        {headline}
      </h2>

      <dl className="sh-board" aria-label={t.scoreboard}>
        <Tile label={t.scPeople} value={score.people} animate={animate} tone="red" />
        <Tile label={t.scMw} value={Math.round(score.mw)} animate={animate} />
        <Tile label={t.scLines} value={score.lines} animate={animate} />
        <Tile label={t.scHospitals} value={score.hospitals} animate={animate} tone={score.hospitals ? 'red' : ''} />
      </dl>

      <ol className="sh-feed" aria-label={t.playByPlay}>
        {allIn && rest && <RestCard rest={rest} lang={lang} animate={animate} />}
        {bigLanded
          .map((p, i) => ({ p, age: bigLanded.length - 1 - i + (allIn && rest ? 1 : 0) }))
          .reverse()
          .map(({ p, age }) => (
            <PlayCard key={p.n} p={p} age={age} lang={lang} animate={animate && age === 0 && newest >= 0} />
          ))}
        {bigLanded.length === 0 && <li className="sh-feed__wait">{t.live}…</li>}
      </ol>
    </>
  )
}

function Tile({ label, value, animate, tone }) {
  const v = useTween(value, { ms: 650, active: animate })
  return (
    <div className={`sh-tile${tone ? ` sh-tile--${tone}` : ''}`}>
      <dt>{label}</dt>
      <dd key={animate ? value : 'still'} className={animate && value ? 'sh-jolt' : undefined}>
        {fmt(v)}
      </dd>
    </div>
  )
}

function PlayCard({ p, age, lang, animate }) {
  const t = S[lang]
  const cls = age === 0 ? 'new' : age === 1 ? 'recent' : 'old'
  const kindLabel = p.kind === 'transformer' ? t.transformer : p.kind === 'storm' ? t.storm : t.line
  const pct = p.loading_pct != null ? Math.round(p.loading_pct) : null
  // REVIEW-1 (c): past ~300 % the figure is a re-solve artefact: words, and a full gauge
  const pctText = pct == null ? null : p.far ? t.farShort : `${fmt(pct)}%`
  return (
    <li className={`sh-play sh-play--${p.kind} sh-play--${cls}${animate ? ' sh-play--live' : ''}`}>
      <div className="sh-play__in">
        <p className="sh-play__row">
          <span className="sh-play__tag">
            {p.kind !== 'storm' ? `${t.play} ${p.n} · ` : ''}
            {kindLabel}
          </span>
          <span className="sh-play__label">{p.kind === 'storm' ? `${fmt(p.count)} ${lang === 'es' ? 'líneas cortadas' : 'lines cut'}` : p.labels[lang] || p.label}</span>
          {pctText && <span className="sh-play__pct">{pctText}</span>}
        </p>
        {(p.people_hit > 0 || p.hospitals > 0 || p.dark > 0) && (
          <div className="sh-play__more sh-play__more--hit">
            <p className="sh-play__hit">
              {p.people_hit > 0 && (
                <strong>
                  +{fmt(p.people_hit)} {t.hit}
                </strong>
              )}
              {p.dark > 0 && <span>{t.subsDark(p.dark)}</span>}
              {p.hospitals > 0 && <span>{t.hospitalsDark(p.hospitals)}</span>}
            </p>
          </div>
        )}
        <div className="sh-play__more">
          <div>
            {pct != null && (
              <p className="sh-play__gauge">
                <Gauge pct={p.far ? 300 : pct} />
                <span>{p.far ? t.farPast : `${fmt(pct)}% ${t.ofItsLimit}`}</span>
              </p>
            )}
            <p className="sh-play__say">{p.say[lang]}</p>
          </div>
        </div>
      </div>
    </li>
  )
}

// the rest of the chain, in one card: how many more lines and transformers tripped, and who they hit
function RestCard({ rest, lang, animate }) {
  const t = S[lang]
  // "6 more lines tripped" / "5 more lines and 1 transformer tripped" / "Se dispararon 6 líneas más"
  const { what, none } = restParts(rest, lang)
  return (
    <li className={`sh-play sh-play--rest sh-play--new${animate ? ' sh-play--live' : ''}`}>
      <div className="sh-play__in">
        <p className="sh-play__row">
          <span className="sh-play__tag">{t.restHead}</span>
          <span className="sh-play__label">{what}</span>
        </p>
        <div className="sh-play__more sh-play__more--hit">
          <p className="sh-play__hit">
            {rest.people > 0 ? (
              <strong>
                +{fmt(rest.people)} {t.hit}
              </strong>
            ) : (
              <span>{none.charAt(0).toUpperCase() + none.slice(1)}</span>
            )}
          </p>
        </div>
      </div>
    </li>
  )
}

// the banner over the map when a big play lands: the crucial piece of infrastructure that fell, and who it hit
function calloutFor(p, t, lang, first) {
  const label = p.labels[lang] || p.label
  const pct = p.loading_pct == null ? '' : p.far ? ` · ${t.farPast}` : ` · ${fmt(Math.round(p.loading_pct))}% ${t.ofItsLimit}`
  const hit = [
    p.people_hit > 0 ? `${p.areas[0] ? `${p.areas[0]} · ` : ''}+${fmt(p.people_hit)} ${t.hit}` : null,
    p.dark > 0 ? t.subsDark(p.dark) : null,
    p.hospitals > 0 ? t.hospitalsDark(p.hospitals) : null,
  ]
    .filter(Boolean)
    .join(' · ')
  if (p.kind === 'storm') return { key: `c${p.n}`, tone: 'amber', head: t.calloutStorm, text: `${fmt(p.count)} ${lang === 'es' ? 'líneas cortadas' : 'lines cut'}`, sub: hit }
  const head = p.kind === 'transformer' ? t.calloutTransformer : t.calloutLine
  return { key: `c${p.n}`, tone: p.kind === 'transformer' || first || p.people_hit > 0 ? 'red' : 'amber', head, text: `${label}${pct}`, sub: hit }
}
