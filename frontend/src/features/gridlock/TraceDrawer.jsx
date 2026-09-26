import { useEffect, useRef, useState } from 'react'
import { ErrorBanner, Loading } from '../../ui'
import { CheckIcon, ConfBadge } from './DetailCard'
import { useGridlock } from './context'
import { displayName, fmtDate, fmtInt, sourceLink, toneOf } from './format'

// "Trace this pair": one pair taken apart, from its rank back to the PDF page. It opens over the pair's steps (Back
// returns to them). Everything here comes from GET /api/gridlock/trace/{id}: the engine recomputes the score's terms
// with the ranking's own functions (and refuses if they don't multiply back to the score), and every provenance field
// is what the committed data holds. The engine's explanations are in English; the frame follows the sheet's language.

const T = {
  en: {
    back: (r) => (r ? `Back to pair ${r}` : 'Back to the pair'),
    eyebrow: 'Trace',
    title: (r) => (r ? `From rank ${r} back to the PDF page` : 'From the score back to the PDF page'),
    lede: "Every number in this pair's ranking and where it comes from, recomputed by the engine with the ranking's own functions.",
    loading: 'Tracing the pair…',
    rank: 'Rank',
    of: (n) => `of ${fmtInt(n)} flagged`,
    outside: 'Outside the current filters (traced anyway)',
    score: 'Score',
    reproduced: 'Reproduces the ranking’s score',
    rule: 'Rule',
    distance: 'Distance',
    closest: 'at the closest points',
    crossing: 'The projects cross',
    on: (id) => `on ${id}`,
    center: "Sperry's center method",
    windows: 'Build windows',
    filed: 'filed',
    derived: 'derived',
    projects: 'The two projects',
    placed: 'Placed on the map',
    notLocated: 'Not located',
    confidence: 'Location confidence (used in the score)',
    drawn: 'Drawn as',
    readFrom: 'Read from',
    page: (p, d) => `page ${p}${d ? `, detail on page ${d}` : ''}`,
    pkg: "In Sperry's package as",
    sha: 'SHA-256, as recorded by the pipeline',
    noSha: 'No hash recorded for this file',
    copy: 'Copy',
    copied: 'Copied',
    raw: 'The row, as the parser read it',
    noRaw: 'No raw text kept for this record.',
    fields: 'Fields, as parsed',
    checks: (s) => `Checks: ${s.passed} passed${s.warned ? `, ${s.warned} warning${s.warned === 1 ? '' : 's'}` : ''}${s.failed ? `, ${s.failed} failed` : ''}`,
    blocking: 'blocking',
    asRecorded: 'As recorded',
    osm: 'OpenStreetMap',
    engineWords: null,
  },
  es: {
    back: (r) => (r ? `Volver al par ${r}` : 'Volver al par'),
    eyebrow: 'Rastreo',
    title: (r) => (r ? `Del puesto ${r} a la página del PDF` : 'De la puntuación a la página del PDF'),
    lede: 'Cada número de la clasificación de este par y de dónde sale, recalculado por el motor con las mismas funciones que la clasificación.',
    loading: 'Rastreando el par…',
    rank: 'Puesto',
    of: (n) => `de ${fmtInt(n)} señalados`,
    outside: 'Fuera de los filtros actuales (rastreado igualmente)',
    score: 'Puntuación',
    reproduced: 'Reproduce la puntuación de la clasificación',
    rule: 'Regla',
    distance: 'Distancia',
    closest: 'entre los puntos más cercanos',
    crossing: 'Los proyectos se cruzan',
    on: (id) => `en ${id}`,
    center: 'Método de centros de Sperry',
    windows: 'Ventanas de obra',
    filed: 'publicada',
    derived: 'derivada',
    projects: 'Los dos proyectos',
    placed: 'Ubicación en el mapa',
    notLocated: 'Sin ubicar',
    confidence: 'Confianza de ubicación (usada en la puntuación)',
    drawn: 'Dibujado como',
    readFrom: 'Leído de',
    page: (p, d) => `página ${p}${d ? `, detalle en la página ${d}` : ''}`,
    pkg: 'En el paquete de Sperry como',
    sha: 'SHA-256, registrado por el proceso',
    noSha: 'No hay hash registrado para este archivo',
    copy: 'Copiar',
    copied: 'Copiado',
    raw: 'La fila, tal como la leyó el analizador',
    noRaw: 'No se guardó texto sin procesar para este registro.',
    fields: 'Campos, tal como se extrajeron',
    checks: (s) => `Comprobaciones: ${s.passed} superadas${s.warned ? `, ${s.warned} con aviso` : ''}${s.failed ? `, ${s.failed} fallidas` : ''}`,
    blocking: 'bloqueante',
    asRecorded: 'Tal como se registró',
    osm: 'OpenStreetMap',
    engineWords: 'Las explicaciones del motor están en inglés.',
  },
}

const TERM_EN = { distance: 'distance', timeline: 'timeline', location: 'location', same_kv: 'same kV' }
const TERM_ES = { distance: 'distancia', timeline: 'calendario', location: 'ubicación', same_kv: 'mismo kV' }
const f2 = (v) => (v == null ? '–' : Number(v).toFixed(2))
// a number to at most k decimals and at least two (0.6 reads 0.60, 0.280321 keeps what it needs)
function fdec(v, k) {
  if (v == null) return '–'
  const [i, d = ''] = Number(v).toFixed(k).replace(/0+$/, '').split('.')
  return `${i}.${d.padEnd(2, '0')}`
}
// The equation as it reads on screen: the raw score to the fewest decimals (2 to 4) that still round to the score, and
// the terms to the fewest (2 to 6) whose product gives exactly that raw score. The distance factor slides within its
// tier, so a fixed 4 decimals could miss (rank 44's 0.2803 gave 9.2499 against 9.2506). If nothing closes it, the
// equation says "≈" instead of "=".
function shownTerms(terms, raw, value) {
  const score = Number(value).toFixed(1)
  // read the way a person rounds (17.15 -> 17.2), not the way binary floats do (17.15 is stored as 17.1499...)
  const rd = [2, 3].find((k) => (Number(Number(raw).toFixed(k)) + 1e-9).toFixed(1) === score) ?? 4
  const target = Number(raw).toFixed(rd)
  for (let k = 2; k <= 6; k++) {
    const shown = terms.map((x) => fdec(x.value, k))
    const prod = shown.reduce((p, s) => p * Number(s), 100)
    if (prod.toFixed(rd) === target) return { shown, exact: true, raw: target }
  }
  return { shown: terms.map((x) => fdec(x.value, 6)), exact: false, raw: target }
}
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1)
const coord = (pt) => (pt ? `${pt[0].toFixed(5)}, ${pt[1].toFixed(5)}` : '–')

export default function TraceDrawer({ id, rank, lang = 'en', onBack }) {
  const g = useGridlock()
  const t = T[lang] || T.en
  const { client, params, pairs } = g
  const b = pairs.map((p) => p[1]).join(',') || 'GPC'
  const { max_km, window_months, method } = params
  const [tries, setTries] = useState(0)
  // the answer is kept with the request it answers: a new pair, setting or retry reads as loading until its own arrives
  const key = `${id}|${max_km}|${window_months}|${method}|${b}|${tries}`
  const [res, setRes] = useState({ key: null, status: 'loading' })
  useEffect(() => {
    if (!client) return undefined
    let live = true
    client.trace(id, { max_km, window_months, method, a: 'DESC', b }).then(
      (data) => live && setRes({ key, status: 'ready', data }),
      (error) => live && setRes({ key, status: 'error', error }),
    )
    return () => {
      live = false
    }
  }, [client, id, max_km, window_months, method, b, key])
  const retry = () => setTries((n) => n + 1)
  const state = res.key === key ? res : { status: 'loading' }
  const d = state.data
  const shownRank = d ? d.rank : rank

  // the heading takes focus when the trace opens (Back returns focus to the button that opened it)
  const h = useRef(null)
  useEffect(() => {
    h.current?.focus({ preventScroll: true })
  }, [])

  const [side, setSide] = useState(0)
  return (
    <div className="gl-trace">
      <header className="gl-trace__head">
        <button type="button" className="gl-back" onClick={onBack}>
          <span aria-hidden="true">‹</span> {t.back(rank)}
        </button>
        <span className="gl-card__eyebrow">{t.eyebrow}</span>
        <h3 ref={h} tabIndex={-1} className="gl-trace__title">
          {t.title(shownRank)}
        </h3>
        <p className="gl-fine">{t.lede}</p>
        {t.engineWords && <p className="gl-fine">{t.engineWords}</p>}
      </header>
      {state.status === 'loading' && <Loading label={t.loading} />}
      {state.status === 'error' && <ErrorBanner error={state.error} onRetry={retry} />}
      {state.status === 'ready' && d && (
        <div className="gl-trace__body">
          <RankSec d={d} t={t} />
          <ScoreSec d={d} t={t} lang={lang} />
          <DistanceSec d={d} t={t} lang={lang} />
          <section className="gl-trace__sec" aria-labelledby="gl-trace-p">
            <h4 id="gl-trace-p" className="gl-trace__h">
              {t.projects}
            </h4>
            <div className="gl-trace__tabs" role="tablist" aria-label={t.projects}>
              {d.projects.map((p, i) => (
                <button
                  key={p.id}
                  type="button"
                  role="tab"
                  id={`gl-trace-tab-${i}`}
                  aria-selected={side === i}
                  aria-controls="gl-trace-panel"
                  className={side === i ? 'is-on' : ''}
                  onClick={() => setSide(i)}
                >
                  <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
                  <span className="gl-trace__tabid">{p.id}</span>
                </button>
              ))}
            </div>
            <div id="gl-trace-panel" role="tabpanel" aria-labelledby={`gl-trace-tab-${side}`}>
              <ProjectTrace key={d.projects[side].id} p={d.projects[side]} t={t} />
            </div>
          </section>
          <p className="gl-fine gl-trace__note">{d.note}</p>
        </div>
      )}
    </div>
  )
}

function RankSec({ d, t }) {
  return (
    <section className="gl-trace__sec gl-trace__sec--row" aria-label={t.rank}>
      <div className="gl-trace__big">
        <span className="gl-trace__k">{t.rank}</span>
        <strong>{d.rank ? `#${d.rank}` : '–'}</strong>
        <span className="gl-fine">{d.rank ? t.of(d.flagged) : t.outside}</span>
      </div>
      <p className="gl-trace__rankrule">
        {d.shared_station ? <span className="gl-stationtag">{d.class_label}: {d.shared_station.name}</span> : <span>{d.class_label}</span>}
        <span className="gl-fine">{d.rank_rule}</span>
      </p>
    </section>
  )
}

function ScoreSec({ d, t, lang }) {
  const s = d.score
  const eq = shownTerms(s.terms, s.raw, s.value)
  const eqSign = eq.exact ? '=' : '≈'
  return (
    <section className="gl-trace__sec" aria-labelledby="gl-trace-s">
      <h4 id="gl-trace-s" className="gl-trace__h">
        {t.score} <strong className="gl-trace__hval">{Number(s.value).toFixed(1)}</strong>
      </h4>
      <p className="gl-trace__eq">
        <span className="gl-trace__eqn">100</span>
        {s.terms.map((x, i) => (
          <span key={x.id} className="gl-trace__eqterm">
            <span className="gl-trace__x">×</span>
            <span className="gl-trace__eqv">
              <strong>{eq.shown[i]}</strong>
              <span>{(lang === 'es' ? TERM_ES : TERM_EN)[x.id] || x.label}</span>
            </span>
          </span>
        ))}
        <span className="gl-trace__eqterm">
          <span className="gl-trace__x">{eqSign}</span>
          <span className="gl-trace__eqv gl-trace__eqv--res">
            <strong>{eq.raw}</strong>
            <span>→ {Number(s.value).toFixed(1)}</span>
          </span>
        </span>
      </p>
      {s.reproduced && (
        <p className="gl-trace__ok">
          <CheckIcon status="pass" /> {t.reproduced}
        </p>
      )}
      <dl className="gl-trace__terms">
        {s.terms.map((x, i) => (
          <div key={x.id}>
            <dt>
              <span>{lang === 'es' ? cap(TERM_ES[x.id] || x.label) : x.label}</span>
              <strong>{eq.shown[i]}</strong>
            </dt>
            <dd>{x.how.charAt(0).toUpperCase() + x.how.slice(1)}</dd>
            <dd className="gl-trace__rule">
              {t.rule}: {x.rule}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function DistanceSec({ d, t, lang }) {
  const x = d.distance
  const [pa, pb] = d.projects
  const big = x.crosses ? t.crossing : `${x.km < 10 ? x.km.toFixed(2) : x.km.toFixed(1)} km (${x.mi < 10 ? x.mi.toFixed(2) : x.mi.toFixed(1)} mi)`
  const win = (w) => (w ? `${fmtDate(w.start, lang)} – ${fmtDate(w.end, lang)}` : '–')
  return (
    <section className="gl-trace__sec" aria-labelledby="gl-trace-d">
      <h4 id="gl-trace-d" className="gl-trace__h">
        {t.distance} <strong className="gl-trace__hval">{big}</strong>
      </h4>
      <dl className="gl-trace__kv">
        <div>
          <dt>{t.closest}</dt>
          <dd>
            <span className="gl-mono">{coord(x.closest_points?.[0])}</span> {t.on(pa.id)}
            <br />
            <span className="gl-mono">{coord(x.closest_points?.[1])}</span> {t.on(pb.id)}
            <span className="gl-fine gl-trace__sub">{x.how}</span>
          </dd>
        </div>
        <div>
          <dt>{t.center}</dt>
          <dd>
            {f2(x.center_mi)} mi ({f2(x.center_km)} km)
            <span className="gl-fine gl-trace__sub">{x.center_how}</span>
          </dd>
        </div>
        <div>
          <dt>{t.windows}</dt>
          <dd>
            {[
              [pa, d.timeline.a_window],
              [pb, d.timeline.b_window],
            ].map(([p, w]) => (
              <span key={p.id} className="gl-trace__win">
                <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
                {win(w)} <span className="gl-fine">({p.window?.filed ? t.filed : t.derived}: {w?.basis || '–'})</span>
              </span>
            ))}
            <span className="gl-fine gl-trace__sub">{d.timeline.reason}</span>
          </dd>
        </div>
      </dl>
    </section>
  )
}

function ProjectTrace({ p, t }) {
  const link = sourceLink(p.source, p.page)
  const [copied, setCopied] = useState(false)
  const copy = () =>
    navigator.clipboard?.writeText(p.source.sha256).then(
      () => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      },
      () => {},
    )
  return (
    <div className="gl-trace__proj">
      <p className="gl-trace__projhead">
        <span className="gl-card__eyebrow">
          <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
          {p.utility_name}
        </span>
        <strong>{displayName(p.name)}</strong>
      </p>

      <div className="gl-trace__block">
        <div className="gl-sec__row">
          <h5>{t.placed}</h5>
          <ConfBadge c={p.confidence} />
        </div>
        <p className="gl-fine">
          {t.confidence}. {p.geometry?.basis ? `${t.drawn}: ${p.geometry.basis}.` : ''}
        </p>
        <ul className="gl-trace__eps">
          {p.endpoints.map((e, i) => (
            <li key={`${e.name}-${i}`} className={e.located ? '' : 'is-off'}>
              <div className="gl-ep__row">
                <strong>{e.name}</strong>
                {e.located ? <ConfBadge c={e.confidence} /> : <span className="gl-conf gl-conf-b--none">{t.notLocated}</span>}
              </div>
              {e.raw && e.raw !== e.name && <span className="gl-fine">&ldquo;{e.raw}&rdquo;</span>}
              {e.osm && (
                <span className="gl-trace__osm">
                  {e.osm.url ? (
                    <a href={e.osm.url} target="_blank" rel="noreferrer">
                      {t.osm} {e.osm.type} {e.osm.id}
                    </a>
                  ) : (
                    `${t.osm} ${e.osm.type} ${e.osm.id}`
                  )}
                  {e.osm.name ? `: ${e.osm.name}` : ''}
                  {e.osm.operator ? ` (${e.osm.operator})` : ''}
                </span>
              )}
              {e.located && (
                <span className="gl-fine gl-mono">
                  {e.lat.toFixed(5)}, {e.lon.toFixed(5)}
                </span>
              )}
              <span className="gl-trace__rulechip">{e.rule.label}</span>
              {e.clauses?.length > 0 && (
                <div className="gl-trace__rec">
                  <span className="gl-fine">{t.asRecorded}:</span>
                  <ul>
                    {e.clauses.map((c, k) => (
                      <li key={k}>{c}</li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="gl-trace__block">
        <h5>{t.readFrom}</h5>
        <p className="gl-trace__src">
          {p.source.title || p.source.id}
          {p.page ? `, ${t.page(p.page, p.detail_page)}` : ''}
          {link && (
            <>
              {' · '}
              <a href={link.url} target="_blank" rel="noreferrer">
                {link.label}
              </a>
            </>
          )}
        </p>
        {p.source.package_file && (
          <p className="gl-fine">
            {t.pkg} <span className="gl-mono">{p.source.package_file}</span>
          </p>
        )}
        <div className="gl-trace__sha">
          <span className="gl-fine">{p.source.sha256 ? t.sha : t.noSha}</span>
          {p.source.sha256 && (
            <span className="gl-trace__shaval">
              <code className="gl-mono">{p.source.sha256}</code>
              <button type="button" className="gl-link" onClick={copy}>
                {copied ? t.copied : t.copy}
              </button>
            </span>
          )}
        </div>
      </div>

      <div className="gl-trace__block">
        <h5>{t.raw}</h5>
        {p.raw_text ? <pre className="gl-raw">{p.raw_text}</pre> : <p className="muted">{t.noRaw}</p>}
      </div>

      <div className="gl-trace__block">
        <h5>{t.fields}</h5>
        <dl className="gl-trace__fields">
          {p.fields.map((x) => (
            <div key={x.field}>
              <dt>{x.label}</dt>
              <dd>
                {x.value} <span className="gl-trace__fid gl-mono">{x.field}</span>
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <details className="gl-details">
        <summary>{t.checks(p.checks_summary)}</summary>
        <ul className="gl-checklist">
          {p.checks.map((c, i) => (
            <li key={`${c.id}-${i}`} className={`gl-check gl-check--${c.status}`}>
              <CheckIcon status={c.status} />
              <span>
                <strong>{c.label}</strong>
                {c.blocking && <span className="gl-block">{t.blocking}</span>} <span className="gl-check__detail">{c.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  )
}
