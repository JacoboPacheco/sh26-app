import { Badge, Button } from '../../ui'
import { bestApply, linesOf } from './stage'
import { FAMILY, T, VERDICT, compact, num, people, splitVerdict } from './text'

// One slide of the review stage: a big number, a headline, one to three short lines, and the slide's
// own treatment (verdict chips on the fix slide; the no-fix band, proof table and split bar; the
// recovery wave strip; "Apply the best fix" on the bottom line). Every word comes from the deck,
// every figure from the engine's report.
export default function Slide({ slide, report, deck, lang, wave, onApply, fixture }) {
  const t = T[lang]
  const kind = slide.kind || slide.id
  const lines = slide.lines?.[lang] || slide.lines?.en || []
  const headline = slide.headline?.[lang] || slide.headline?.en || ''
  const big = slide.big
  const apply = kind === 'bottom_line' && onApply ? slide.map?.apply || bestApply(report) : null
  const written = slide.written_by?.[lang]
  const checked = deck?.ai?.numbers_checked || 0

  return (
    <article className={`rs-slide rs-slide--${kind}`} aria-labelledby={`rs-h-${slide.id}`}>
      {kind === 'no_fix' ? (
        <h2 className="rs-nofix-band" id={`rs-h-${slide.id}`}>
          {headline}
        </h2>
      ) : (
        <>
          {big && (
            <p className={`rs-big rs-big--${big.tone || 'neutral'}`}>
              <span className="rs-big__n">{big.display?.[lang] || big.display?.en || num(big.value)}</span>
              <span className="rs-big__label">{big.label?.[lang] || big.label?.en}</span>
            </p>
          )}
          <h2 className="rs-headline" id={`rs-h-${slide.id}`}>
            {headline}
          </h2>
        </>
      )}

      {kind === 'no_fix' && report?.no_fix && <ProofTable report={report} lang={lang} />}
      {kind === 'no_fix' && report?.split && <SplitBar split={report.split} lang={lang} />}

      {lines.length > 0 && !(kind === 'no_fix' && report?.no_fix?.proof?.length) && (
        <ul className="rs-lines">
          {lines.map((line, i) => {
            const { text, verdict } = kind === 'fix' ? splitVerdict(line, lang) : { text: line, verdict: null }
            return (
              <li key={i}>
                <span>{text}</span>
                {verdict && <span className={`rs-chip rs-chip--${verdict}`}>{VERDICT[lang][verdict]}</span>}
              </li>
            )
          })}
        </ul>
      )}

      {kind === 'recovery' && report?.recovery?.waves?.length > 0 && <WaveStrip recovery={report.recovery} wave={wave} lang={lang} />}

      {kind === 'bottom_line' && report && <Takeaway report={report} lang={lang} />}

      {apply && (
        <div className="rs-apply">
          <Button onClick={() => onApply(apply)}>{t.applyBest}</Button>
        </div>
      )}

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
    if (ev.people > 0) rows.push([t.ifNothing, t.peopleOut(people(ev.people, lang))])
  }
  if (!rows.length) return null
  return (
    <dl className={nf ? 'rs-takeaway rs-takeaway--nofix' : 'rs-takeaway'} aria-label={t.takeaway}>
      {rows.map(([k, v]) => (
        <div key={k} className="rs-takeaway__row">
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
        {rows.map((r) => (
          <tr key={r.family}>
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
