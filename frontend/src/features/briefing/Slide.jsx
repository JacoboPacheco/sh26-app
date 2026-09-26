import { useEffect, useState } from 'react'
import { Badge, Button } from '../../ui'
import { Count, Gauge, Kicker } from './ShowBits'
import { AreaTour, CostLayer, EventLayer, HospitalsLayer, NoFixLayer, StormLayer } from './ShowBeats'
import ShowBottom from './ShowBottom'
import ShowChain from './ShowChain'
import ShowSolutions from './ShowSolutions'
import ShowToll from './ShowToll'
import ShowWeakPoint from './ShowWeakPoint'
import { S } from './showText'
import { bestApply, linesOf } from './stage'
import { reportPeople } from '../cost/figures'
import { FAMILY, T, VERDICT, compact, num, people, splitVerdict } from './text'
import { useElapsed } from './useShowClock'

// One slide of the review stage. The play-by-play show's beats (the toll, the chain, the solutions) are
// their own components; every other slide keeps the same words and figures with things that move: the big
// number counts up, the lines rise in one after another, bars draw in, gauges fill. `animate` is true while
// the show runs; a paused or scrubbed slide (animate false) is the finished picture.
export default function Slide({ slide, report, deck, lang, wave, onApply, fixture, stage, live, animate = false, options = [] }) {
  const t = T[lang]
  const kind = slide.kind || slide.id
  const lines = slide.lines?.[lang] || slide.lines?.en || []
  const headline = slide.headline?.[lang] || slide.headline?.en || ''
  const big = slide.big
  // a catastrophe with no fix offers the rebuild plan, not a button that would read as a rescue
  const apply = kind === 'bottom_line' && onApply ? slide.map?.apply || (report?.no_fix ? null : bestApply(report)) : null
  const written = slide.written_by?.[lang]
  const checked = deck?.ai?.numbers_checked || 0
  const noFix = !!report?.no_fix
  const nSolutions = options.length
  const [areaOn, setAreaOn] = useState(null) // the area the map's tour is on (the areas slide)
  const fixSlide = (deck?.slides || []).find((x) => (x.kind || x.id) === 'fix')

  let body
  if (kind === 'toll') {
    body = <ShowToll slide={slide} deck={deck} report={report} lang={lang} animate={animate} stage={stage} />
  } else if (kind === 'cause' && slide.weak_point && report?.root_cause) {
    body = <ShowWeakPoint slide={slide} report={report} lang={lang} animate={animate} stage={stage} />
  } else if (kind === 'bottom_line' && !noFix && nSolutions > 0) {
    const more = (slide.lines?.[lang] || slide.lines?.en || []).filter((x) => !/^(Apply|Aplica)/.test(x))
    body = (
      <>
        <h2 className="rs-headline" id={`rs-h-${slide.id}`}>
          {headline}
        </h2>
        <ShowBottom fixSlide={fixSlide} report={report} lang={lang} animate={animate} options={options} stage={stage} />
        {more.length > 0 && <Lines lines={more} lang={lang} />}
        {apply && (
          <div className="rs-apply">
            <Button onClick={() => onApply(apply)}>{t.applyBest}</Button>
          </div>
        )}
      </>
    )
  } else if (kind === 'chain') {
    body = <ShowChain slide={slide} report={report} lang={lang} animate={animate} stage={stage} cueStep={live?.cueStep} />
  } else if (kind === 'fix' && nSolutions > 0) {
    body = (
      <>
        <ShowSolutions slide={slide} report={report} lang={lang} animate={animate} options={options} stage={stage} live={live || {}} agentic={deck?.agentic} />
        {!animate && <AlsoTested report={report} lang={lang} />}
      </>
    )
  } else if (kind === 'no_fix') {
    body = (
      <>
        <h2 className="rs-nofix-band" id={`rs-h-${slide.id}`}>
          {headline}
        </h2>
        <NoFixLayer report={report} lang={lang} stage={stage} animate={animate} />
        {report?.no_fix && <ProofTable report={report} lang={lang} />}
        {report?.split && <SplitBar split={report.split} lang={lang} />}
        {!(report?.no_fix?.proof?.length) && <Lines lines={lines} />}
      </>
    )
  } else {
    body = (
      <>
        {big && <BigNumber big={big} lang={lang} animate={animate} />}
        <h2 className="rs-headline" id={`rs-h-${slide.id}`}>
          {headline}
        </h2>
        {kind === 'areas' && report?.areas?.length > 0 ? (
          <>
            <AreaTour report={report} lang={lang} animate={animate} stage={stage} onArea={setAreaOn} />
            <AreaBars areas={report.areas.filter((a) => Number(a.people) > 0).slice(0, 5)} lang={lang} animate={animate} on={animate ? areaOn : null} />
          </>
        ) : null}
        {kind === 'event' && <EventLayer report={report} lang={lang} stage={stage} animate={animate} />}
        {kind === 'hospitals' && <HospitalsLayer report={report} lang={lang} stage={stage} animate={animate} />}
        {kind === 'cost' && <CostLayer report={report} lang={lang} stage={stage} animate={animate} />}
        {kind === 'cause' && report?.root_cause?.cause === 'storm' && <StormLayer report={report} stage={stage} animate={animate} />}
        {kind === 'cause' && report?.root_cause?.pct_with != null ? <CauseGauges rc={report.root_cause} lang={lang} /> : null}
        {!(kind === 'areas' && report?.areas?.length) && !(kind === 'cause' && report?.root_cause?.pct_with != null) && (
          <Lines lines={lines} verdicts={kind === 'fix'} lang={lang} />
        )}
        {kind === 'recovery' && report?.recovery?.waves?.length > 0 && <WaveStrip recovery={report.recovery} wave={wave} lang={lang} />}
        {apply && (
          <div className="rs-apply">
            <Button onClick={() => onApply(apply)}>{t.applyBest}</Button>
          </div>
        )}
        {kind === 'bottom_line' && noFix && report?.recovery?.waves?.length > 0 && <Recovery report={report} lang={lang} animate={animate} stage={stage} wave={wave} />}
        {kind === 'bottom_line' && report && <Takeaway report={report} lang={lang} />}
      </>
    )
  }

  return (
    <article className={`rs-slide rs-slide--${kind}${animate ? ' rs-slide--live' : ''}`} aria-labelledby={`rs-h-${slide.id}`}>
      {body}
      <footer className="rs-slide__foot">
        {fixture ? (
          <Badge tone="warn">{t.fixture}</Badge>
        ) : written === 'gemini' ? (
          <Badge>{t.byGemini(checked)}</Badge>
        ) : (
          <Badge tone="warn">{t.template}</Badge>
        )}
      </footer>
    </article>
  )
}

// the deck's lines, rising in one after another
function Lines({ lines, verdicts = false, lang }) {
  if (!lines.length) return null
  return (
    <ul className="rs-lines sh-stagger">
      {lines.map((line, i) => {
        const { text, verdict } = verdicts ? splitVerdict(line, lang) : { text: line, verdict: null }
        return (
          <li key={i} style={{ '--i': i + 2 }}>
            <span>{text}</span>
            {verdict && <span className={`rs-chip rs-chip--${verdict}`}>{VERDICT[lang][verdict]}</span>}
          </li>
        )
      })}
    </ul>
  )
}

// the slide's big figure; a plain number (or a percentage / MW) counts up to itself
function BigNumber({ big, lang, animate }) {
  const disp = big.display?.[lang] || big.display?.en || num(big.value)
  const m = /^([\d,]+)(.*)$/.exec(disp)
  const n = m ? Number(m[1].replace(/,/g, '')) : NaN
  return (
    <p className={`rs-big rs-big--${big.tone || 'neutral'}`}>
      <span className="rs-big__n">
        {Number.isFinite(n) ? (
          <>
            <span aria-hidden="true">
              <Count value={n} ms={1100} delay={250} active={animate} />
              {m[2]}
            </span>
            <span className="sh-sr">{disp}</span>
          </>
        ) : (
          disp
        )}
      </span>
      <span className="rs-big__label">{big.label?.[lang] || big.label?.en}</span>
    </p>
  )
}

// who is hit hardest: one bar per area, longest first, counting up
function AreaBars({ areas, lang, animate, on = null }) {
  const t = T[lang]
  const max = Math.max(1, ...areas.map((a) => Number(a.people) || 0))
  return (
    <ol className="sh-areas">
      {areas.map((a, i) => (
        <li
          key={a.area}
          className={on && String(a.area).toLowerCase() === on ? 'sh-areas__on' : undefined}
          style={{ '--i': i, '--w': `${((Number(a.people) || 0) / max) * 100}%` }}
        >
          <span className="sh-areas__name">{a.area}</span>
          <b className="sh-areas__n">
            <Count value={a.people} ms={1100} delay={300 + i * 180} active={animate} />
          </b>
          <span className="sh-areas__bar" aria-hidden="true">
            <i />
          </span>
        </li>
      ))}
      <li className="sh-areas__foot">{t.stillOut === 'out' ? 'people without power (estimate)' : 'personas sin luz (estimación)'}</li>
    </ol>
  )
}

// why it failed: the first line's loading with the data center against without it
function CauseGauges({ rc, lang }) {
  const t = S[lang]
  const rows = [
    [t.withCampus, rc.pct_with, 500, undefined],
    rc.pct_without != null ? [t.withoutCampus, rc.pct_without, 1100, 'calm'] : null,
  ].filter(Boolean)
  return (
    <div className="sh-cause">
      {rows.map(([label, pct, delay, tone]) => (
        <div key={label} className="sh-cause__row">
          <span>{label}</span>
          <b>{Math.round(pct)}%</b>
          <Gauge pct={pct} delay={delay} tone={tone} />
        </div>
      ))}
      <p className="sh-note">
        {t.limit}: 100%
        {rc.campus_share_pct != null ? ` · ${Math.round(rc.campus_share_pct)}% ${t.campusShare}` : ''}
      </p>
    </div>
  )
}

// what was tested and did not hold, in one line
function AlsoTested({ report, lang }) {
  const bad = (report?.fixes || []).filter((f) => f.verdict === 'fails')
  if (!bad.length) return null
  return (
    <p className="sh-also">
      {lang === 'es' ? 'También probadas, no funcionan: ' : 'Also tested, did not hold: '}
      {bad.map((f) => FAMILY[lang][f.family] || f.family).join(' · ')}
    </p>
  )
}

// no fix: the rebuild plan, wave by wave, lighting the rebuilt lines on the map in green
function Recovery({ report, lang, animate, stage, wave }) {
  const s = S[lang]
  const waves = report.recovery.waves
  const clock = useElapsed(animate, 900 + waves.length * 1400 + 500)
  const n = animate ? Math.min(waves.length, Math.max(0, Math.floor((clock - 700) / 1400) + 1)) : waves.length
  useEffect(() => {
    if (animate && stage) stage.wave(n)
  }, [animate, n, stage])
  // a wave's people back count everyone its lines and the earlier waves' bring back (running total)
  const back = waves.filter((w) => w.n <= n).reduce((a, w) => Math.max(a, Number(w.people_back) || 0), 0)
  return (
    <section className="sh-restore">
      <Kicker tone="green">{s.restore}</Kicker>
      <WaveStrip recovery={report.recovery} wave={animate ? n : wave} lang={lang} />
      <p className="sh-restore__back">
        <b>
          <Count value={back} ms={900} active={animate} />
        </b>{' '}
        {s.lightsBack}
      </p>
    </section>
  )
}

// The bottom line's recommendation, as facts: the verified best fix and what the re-run gave, the other
// fixes that hold, and what happens if nothing changes; for a catastrophe, who no fix can reach, what
// to rebuild first and what to harden. Every figure is the engine's.
function Takeaway({ report, lang }) {
  const t = T[lang]
  const rows = []
  const ev = report.event || {}
  const nf = report.no_fix
  const fixes = report.fixes || []
  const best = report.best_fix != null ? fixes[report.best_fix] : null
  const name = (f) => (lang === 'en' ? f.action || f.label : FAMILY.es[f.family] || f.action)
  if (nf) {
    rows.push([t.unreachable, t.unreachableV(people(nf.people, lang))])
    const w = report.recovery?.waves?.[0]
    const km = w ? (w.km_total ?? w.km) : null
    if (w) rows.push([t.rebuildFirst, t.rebuildFirstV(num(linesOf(w)), km != null ? num(Math.round(km)) : null, people(w.people_back, lang))])
    const base = report.recovery?.baseline
    if (base?.plan_better_by > 0 && base.repairs) rows.push([t.whyOrder, t.orderBeats(num(base.repairs), people(base.plan_better_by, lang))])
    const h = report.recovery?.hardening?.[0]
    if (h?.people_kept_on > 0) rows.push([t.harden, t.hardenV(num(h.k), people(h.people_kept_on, lang))])
  } else if (best && best.outcome && (best.verdict === 'holds' || best.verdict === 'partly')) {
    rows.push([t.bestFix, `${name(best)} · ${t.rerun(best.outcome.steps, people(best.outcome.people, lang))}`])
    const others = fixes.filter((f, i) => i !== report.best_fix && f.verdict === 'holds' && f.family !== 'remove').slice(0, 2)
    if (others.length) rows.push([t.alsoHolds, others.map(name).join(' · ')])
    const { hit, stillOut } = reportPeople(report)
    if (ev.people > 0) rows.push([t.ifNothing, t.ifNothingV(num(hit), num(stillOut))])
  }
  if (!rows.length) return null
  return (
    <dl className={nf ? 'rs-takeaway rs-takeaway--nofix' : 'rs-takeaway'} aria-label={t.takeaway}>
      {rows.map(([k, v], i) => (
        <div key={k} className="rs-takeaway__row" style={{ '--i': i + 3 }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function ProofTable({ report, lang }) {
  const t = T[lang]
  const rows = report.no_fix.proof || []
  if (!rows.length) return null
  return (
    <table className="rs-proof">
      <caption>{t.proof}</caption>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r.family} style={{ '--i': i }}>
            <th scope="row">{FAMILY[lang][r.family] || r.family}</th>
            <td>
              {/* "holds" here means the cascade stops; the people the damage cut off stay dark, so it isn't a rescue */}
              <span className={`rs-chip rs-chip--${r.verdict}`}>{r.verdict === 'holds' ? t.stopsCascade : VERDICT[lang][r.verdict] || r.verdict}</span>
            </td>
            <td className="rs-proof__n">
              {r.people != null && (
                <>
                  {people(r.people, lang)} {t.stillOut}
                </>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function SplitBar({ split, lang }) {
  const t = T[lang]
  const parts = [
    ['physical', split.physical],
    ['cascade', split.cascade],
    ['campus', split.campus],
  ].filter(([, v]) => v > 0)
  const total = parts.reduce((n, [, v]) => n + v, 0)
  if (!total) return null
  return (
    <figure className="rs-split">
      <figcaption>{t.split}</figcaption>
      <div className="rs-split__bar" aria-hidden="true">
        {parts.map(([k, v]) => (
          <span key={k} className={`rs-split__part rs-split__part--${k}`} style={{ '--w': `${(v / total) * 100}%` }} />
        ))}
      </div>
      <ul className="rs-split__key">
        {parts.map(([k, v]) => (
          <li key={k}>
            <span className={`rs-swatch rs-swatch--${k}`} aria-hidden="true" />
            {t[k]}: {people(v, lang)}
          </li>
        ))}
      </ul>
    </figure>
  )
}

function WaveStrip({ recovery, wave, lang }) {
  const t = T[lang]
  return (
    <figure className="rs-waves-fig">
      <figcaption>{t.waves}</figcaption>
      <ol className="rs-waves">
        {recovery.waves.map((w) => {
          const count = linesOf(w)
          return (
            <li key={w.n} className={w.n <= wave ? 'rs-wave rs-wave--lit' : 'rs-wave'} title={`${num(count)} ${t.linesInAll}: ${num(w.people_back)} ${t.back}`}>
              <span className="rs-wave__n">
                {t.wave} {w.n}
              </span>
              {count != null && (
                <span>
                  {num(count)} {t.lines}
                </span>
              )}
              <strong>{compact(w.people_back)}</strong>
              <span className="rs-wave__label">{t.back}</span>
            </li>
          )
        })}
      </ol>
    </figure>
  )
}
