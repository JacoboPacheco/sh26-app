import { useOverload } from '../../store'
import { Badge, Button } from '../../ui'
import { SplitBar } from './Slide'
import { loc } from './stage'
import { FAMILY, T, VERDICT, num, people, usd } from './text'

// The full written briefing: the report laid out as a document, top to bottom. The timeline is synced
// to the map (the step on screen is marked; a row click shows that step). Fixes carry their verified
// verdict and, when the engine gives one, an "Apply this fix" button. Prints cleanly (briefing.css).
// Without the report (engine not live), the deck's own words stand in.
export default function BriefingDoc({ report, deck, lang, stepIdx, onApply, fixture }) {
  const t = T[lang]
  const { step, setStep, cascade } = useOverload()
  if (!report) return <DeckDoc deck={deck} lang={lang} fixture={fixture} />
  const ev = report.event || {}
  const c = report.case || {}
  const rc = report.root_cause
  const fixes = report.fixes || []
  const rec = report.recovery
  const nf = report.no_fix
  const best = report.best_fix

  return (
    <article className="rs-doc" aria-labelledby="rs-doc-title">
      <p className="rs-doc__banner">{report.banner}</p>
      <header className="rs-doc__head">
        <p className="rs-doc__kicker">
          {t.sim} · {deck?.title?.[lang] || 'Incident briefing'}
        </p>
        <h2 id="rs-doc-title">{report.headline?.text}</h2>
        <div className="row">
          <Badge tone={report.verdict === 'preventable' ? 'neutral' : 'warn'}>{VERDICT_LABEL[report.verdict] || report.verdict}</Badge>
          {fixture && <Badge tone="warn">{t.fixture}</Badge>}
          <Button variant="secondary" onClick={() => window.print()}>
            Print
          </Button>
        </div>
      </header>

      <section aria-labelledby="rs-doc-sum">
        <h3 id="rs-doc-sum">Summary</h3>
        <dl className="rs-facts">
          <Fact k="People without power at the end" v={`${num(ev.people)} (estimate)`} />
          {ev.peak_people > ev.people && <Fact k="At the worst step" v={`${num(ev.peak_people)} (estimate)`} />}
          {ev.people_share_pct != null && <Fact k={`Share of ${report.region_name || 'the state'}`} v={`${ev.people_share_pct}% (estimate)`} />}
          <Fact k="Existing load lost" v={`${num(ev.lost_mw)} MW`} />
          <Fact k="Cascade steps" v={`${num(ev.steps)}${ev.capped ? ' (still spreading when the model stopped)' : ''}`} />
          {ev.storm_lines_out > 0 && <Fact k="Lines knocked out by the storm" v={num(ev.storm_lines_out)} />}
          {c.mw > 0 && <Fact k="Data center" v={`${num(c.mw)} MW at ${c.sub_name ? titleCase(c.sub_name) : 'the site'}`} />}
          {c.load_word && <Fact k="Load level" v={c.load_word} />}
          {c.preset?.name && <Fact k="Scenario" v={`${c.preset.name} (hypothetical)`} />}
        </dl>
      </section>

      {report.timeline?.length > 0 && (
        <section aria-labelledby="rs-doc-tl">
          <h3 id="rs-doc-tl">What happened, step by step</h3>
          <ol className="rs-tl">
            {report.timeline.map((row) => {
              const si = stepIdx(row.n)
              const now = cascade && si != null && si === step
              return (
                <li key={row.n} className={now ? 'rs-tl__row rs-tl__row--now' : 'rs-tl__row'}>
                  <button type="button" className="rs-tl__btn" onClick={() => si != null && setStep(si)} disabled={!cascade || si == null} aria-current={now ? 'step' : undefined}>
                    <span className="rs-tl__n">{row.action === 'storm' ? 'Storm' : `Step ${row.n}`}</span>
                    <span className="rs-tl__what">
                      {row.action === 'storm'
                        ? `${num(row.storm_lines?.count ?? row.storm_lines ?? row.lines?.length)} lines knocked out by the storm`
                        : row.lines?.map((l) => `${capital(l.label)}${l.pct_before ? ` trips at ${Math.round(l.pct_before)}%` : ''}`).join('; ')}
                      {row.action === 'shed' && ' (customers cut to hold a line)'}
                    </span>
                    {row.newly_dark?.length > 0 && <span className="rs-tl__dark">Power lost (estimates): {darkList(row.newly_dark)}</span>}
                    <span className="rs-tl__cum">{num(row.people_cum)} people out (estimate)</span>
                  </button>
                </li>
              )
            })}
          </ol>
        </section>
      )}

      {rc?.line && (
        <section aria-labelledby="rs-doc-rc">
          <h3 id="rs-doc-rc">Why it happened</h3>
          <p>{rc.sentence || `The first line to fail was ${rc.line.label}.`}</p>
          <dl className="rs-facts">
            <Fact k="First line to fail" v={capital(rc.line.label)} />
            {rc.pct_with != null && <Fact k="Loading with the data center" v={`${rc.pct_with}%`} />}
            {rc.pct_without != null && <Fact k="Loading without it" v={`${rc.pct_without}%`} />}
            {rc.campus_share_pct != null && <Fact k="The data center's share of its flow" v={`${rc.campus_share_pct}%`} />}
            {rc.cause && <Fact k="Cause" v={CAUSE[rc.cause] || rc.cause} />}
          </dl>
        </section>
      )}

      {report.areas?.length > 0 && (
        <section aria-labelledby="rs-doc-areas">
          <h3 id="rs-doc-areas">Who was hit</h3>
          <table className="rs-table">
            <thead>
              <tr>
                <th scope="col">Area (substations named after it)</th>
                <th scope="col">People without power (estimate)</th>
                <th scope="col">Load lost</th>
              </tr>
            </thead>
            <tbody>
              {report.areas.map((a) => (
                <tr key={a.area}>
                  <th scope="row">{a.area}</th>
                  <td>{num(a.people)}</td>
                  <td>{num(a.mw)} MW</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.hospitals?.count > 0 && (
            <p className="muted">
              {num(report.hospitals.count)} hospitals are in the affected areas and would run on backup power (count only; {report.hospitals.source}).
            </p>
          )}
        </section>
      )}

      {report.cost && (
        <section aria-labelledby="rs-doc-cost">
          <h3 id="rs-doc-cost">What it costs (estimate)</h3>
          <dl className="rs-facts">
            {report.cost.blackout_usd != null && (
              <Fact k={`The blackout, if it lasts ${report.cost.duration_h_assumed} hours (assumed)`} v={`${usd(report.cost.blackout_usd)} (estimate)`} />
            )}
            {report.cost.upgrade_usd != null && <Fact k="The upgrades that prevent it" v={`${usd(report.cost.upgrade_usd)} (estimate)`} />}
            {report.cost.campus_bill_usd_per_year != null && <Fact k="The data center's power bill per year" v={`${usd(report.cost.campus_bill_usd_per_year)} (estimate)`} />}
            {report.cost.who_pays && <Fact k="Who pays" v={report.cost.who_pays} />}
          </dl>
          {report.cost.assumptions?.length > 0 && (
            <ul className="rs-notes">
              {report.cost.assumptions.map((a) => (
                <li key={a.key}>
                  {a.note || a.key}: {typeof a.value === 'number' ? num(a.value) : a.value} {a.unit}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {fixes.length > 0 && (
        <section aria-labelledby="rs-doc-fix">
          <h3 id="rs-doc-fix">How to fix it (each one re-run on the model)</h3>
          <ul className="rs-fixes">
            {fixes.map((f, i) => (
              <li key={`${f.family}${i}`} className={i === best ? 'rs-fix rs-fix--best' : 'rs-fix'}>
                <div className="rs-fix__top">
                  <strong>{f.action || f.label}</strong>
                  <span className={`rs-chip rs-chip--${f.verdict}`}>{VERDICT.en[f.verdict] || f.verdict}</span>
                  {i === best && <Badge>Best fix</Badge>}
                </div>
                <p className="muted">
                  {FAMILY.en[f.family] || f.label}
                  {f.outcome && f.verdict !== 'not_checked' && f.verdict !== 'not_needed' && (
                    <>
                      {' · '}
                      {f.outcome.steps} steps, {num(f.outcome.people)} people out (estimate)
                    </>
                  )}
                  {f.tradeoff && ` · ${f.tradeoff}`}
                </p>
                {onApply && f.apply && f.verdict === 'holds' && (
                  <Button variant="secondary" onClick={() => onApply(f.apply)}>
                    {t.apply}
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {report.firm_note && (
            <p className="muted">
              Firm service instead: keeping the data center on means cutting {num(report.firm_note.shed_mw)} MW of other customers (
              {num(report.firm_note.people)} people, estimate).
            </p>
          )}
          {report.unchecked?.length > 0 && <p className="muted">Not checked in time: {report.unchecked.map((f) => FAMILY.en[f] || f).join(', ')}.</p>}
        </section>
      )}

      {nf && (
        <section aria-labelledby="rs-doc-nofix" className="rs-doc__nofix">
          <h3 id="rs-doc-nofix">No fix exists for about {people(nf.people)} people (estimate)</h3>
          <p>{nf.sentence}</p>
          {report.split && <SplitBar split={report.split} lang="en" />}
        </section>
      )}

      {rec && (
        <section aria-labelledby="rs-doc-rec">
          <h3 id="rs-doc-rec">Restoration: rebuild in this order</h3>
          {rec.method !== 'lp' && <p className="muted">Line limits not applied in this plan (a connectivity estimate, not verified).</p>}
          <table className="rs-table">
            <thead>
              <tr>
                <th scope="col">Wave</th>
                <th scope="col">Lines rebuilt</th>
                <th scope="col">Length</th>
                <th scope="col">People back (estimate)</th>
                <th scope="col">Still out (estimate)</th>
              </tr>
            </thead>
            <tbody>
              {rec.waves.map((w) => (
                <tr key={w.n}>
                  <th scope="row">{w.n}</th>
                  <td>{num(Array.isArray(w.lines) && w.lines.length ? w.lines.length : w.line_count)}</td>
                  <td>{w.km != null ? `${num(w.km)} km` : '–'}</td>
                  <td>{num(w.people_back)}</td>
                  <td>{num(w.people_out)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rec.hardening?.length > 0 && (
            <p>
              Hardening before the next storm:{' '}
              {rec.hardening.map((h) => `${h.k} lines keep ${num(h.people_kept_on)} people on (estimate)`).join('; ')}.
            </p>
          )}
        </section>
      )}

      <footer className="rs-doc__foot">
        <p>{deck?.credit || 'Grid: Breakthrough Energy Sciences U.S. Test System, from Texas A&M ACTIVSg synthetic grids (CC-BY 4.0).'}</p>
        <p>A simulation on a synthetic grid model: not any utility&apos;s network, not a real alert. Every people and cost number is an estimate.</p>
      </footer>
    </article>
  )
}

const VERDICT_LABEL = { preventable: 'Preventable (verified)', partly: 'Partly preventable', no_fix: 'No fix exists', nothing_happened: 'Nothing tripped' }
const CAUSE = {
  campus: 'The data center (the line is fine without it)',
  last_straw: 'The last straw (the line was already near its limit)',
  heat: 'The heat (it cascades even without the data center)',
  storm: 'The storm',
  none: 'None',
}

function Fact({ k, v }) {
  return (
    <div className="rs-fact">
      <dt>{k}</dt>
      <dd>{v}</dd>
    </div>
  )
}

// the areas that lost power at a step, the largest first (a storm darkens a hundred at once)
function darkList(list) {
  const top = [...list].sort((a, b) => (b.people || 0) - (a.people || 0))
  const shown = top.slice(0, 6).map((d) => `${d.area} (${num(d.people)})`)
  return top.length > 6 ? `${shown.join(', ')} and ${top.length - 6} more areas` : shown.join(', ')
}

const capital = (s) => (s ? s[0].toUpperCase() + s.slice(1) : '')
const titleCase = (s) => String(s).toLowerCase().replace(/\b\w/g, (ch) => ch.toUpperCase())

// No report: the deck, slide by slide, as text
function DeckDoc({ deck, lang, fixture }) {
  const t = T[lang]
  if (!deck) return null
  return (
    <article className="rs-doc" aria-labelledby="rs-doc-title">
      <p className="rs-doc__banner">{loc(deck, 'banner', lang)}</p>
      <header className="rs-doc__head">
        <p className="rs-doc__kicker">{t.sim}</p>
        <h2 id="rs-doc-title">{deck.title?.[lang]}</h2>
        {fixture && <Badge tone="warn">{t.fixture}</Badge>}
      </header>
      {deck.slides.map((s) => (
        <section key={s.id}>
          <h3>{s.headline?.[lang]}</h3>
          <ul className="rs-notes">
            {(s.lines?.[lang] || []).map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </section>
      ))}
      <footer className="rs-doc__foot">
        <p>{loc(deck, 'credit', lang)}</p>
        <p>{loc(deck, 'disclaimer', lang)}</p>
      </footer>
    </article>
  )
}
