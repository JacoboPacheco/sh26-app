import { useEffect, useMemo, useRef, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Gauge, Kicker } from './ShowBits'
import { playsOf, scoreAt } from './showDeck'
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

// The chain as a broadcast: each tripped line is a play card sliding into the feed while the scoreboard
// ticks, and the map plays the same blast. The plays land on the map's own clock (its replay schedule),
// or on the narration's step cues, whichever is ahead, so the cards and the map never disagree.
export default function ShowChain({ slide, report, lang, animate, stage, cueStep }) {
  const t = S[lang]
  const o = useOverload()
  const cascade = o.cascade
  const plays = useMemo(() => playsOf(report, slide, cascade), [report, slide, cascade])
  const [shown, setShown] = useState(animate ? 0 : plays.length)
  const fxRef = useRef(null)
  const cueRef = useRef(0)
  useEffect(() => {
    fxRef.current = o.fx
    cueRef.current = cueStep || 0
  })
  const headline = slide.headline?.[lang] || slide.headline?.en || ''

  // which plays have landed
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

  // the scoreboard and the map's banner follow the newest play
  const score = scoreAt(plays, shown)
  useEffect(() => {
    stage.setPlay(shown)
    if (!animate || shown === 0) return
    const p = plays[shown - 1]
    stage.callout(calloutFor(p, t, lang, shown === 1))
    stage.say({ key: `play-${p.n}`, text: p.say[lang] }) // shown whenever the narration isn't speaking
  }, [shown, animate, plays, lang, stage, t])
  useEffect(
    () => () => {
      stage.callout(null)
      stage.say(null)
    },
    [stage],
  )

  const newest = shown - 1
  const items = plays.slice(0, shown)
  return (
    <>
      <div className="sh-headrow">
        <Kicker tone="red">{t.playByPlay}</Kicker>
        <span className="sh-count">
          {t.play} {Math.max(shown, animate ? 0 : plays.length)} {t.of} {plays.length}
        </span>
      </div>
      <div className="sh-drive" aria-hidden="true">
        {plays.map((p, i) => (
          <i key={p.n} className={i < shown ? (i === newest && animate ? 'on new' : 'on') : ''} />
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
        {items
          .map((p, i) => ({ p, age: shown - 1 - i }))
          .reverse()
          .map(({ p, age }) => (
            <PlayCard key={p.n} p={p} age={age} lang={lang} animate={animate} />
          ))}
        {shown === 0 && <li className="sh-feed__wait">{t.live}…</li>}
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
  return (
    <li className={`sh-play sh-play--${p.kind} sh-play--${cls}${animate ? ' sh-play--live' : ''}`}>
      <div className="sh-play__in">
        <p className="sh-play__row">
          <span className="sh-play__tag">
            {p.kind !== 'storm' ? `${t.play} ${p.n} · ` : ''}
            {kindLabel}
          </span>
          <span className="sh-play__label">{p.kind === 'storm' ? `${fmt(p.count)} ${lang === 'es' ? 'líneas cortadas' : 'lines cut'}` : p.labels[lang] || p.label}</span>
          {pct != null && <span className="sh-play__pct">{fmt(pct)}%</span>}
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
                <Gauge pct={pct} />
                <span>
                  {fmt(pct)}% {t.ofItsLimit}
                </span>
              </p>
            )}
            <p className="sh-play__say">{p.say[lang]}</p>
          </div>
        </div>
      </div>
    </li>
  )
}

// the banner over the map when a play lands: the crucial piece of infrastructure that fell, and who it hit
function calloutFor(p, t, lang, first) {
  const label = p.labels[lang] || p.label
  const pct = p.loading_pct != null ? ` · ${fmt(Math.round(p.loading_pct))}% ${t.ofItsLimit}` : ''
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
