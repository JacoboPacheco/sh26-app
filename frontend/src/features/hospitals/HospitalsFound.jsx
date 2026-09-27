// FEATURE: the hospitals beat's evidence — each hospital in the areas that lost power (on the SYNTHETIC model; that
// it runs on backup power is assumed, never stated as fact) with its beds AS REPORTED and where the figure comes
// from, found by the hospital beds agent (Gemini with Google Search) and checked in code:
//   reported   the figure a web page states (the link); "read on the page" when the checker found the number on that
//              page as this hospital's bed count (its words in the tooltip), else "Google tied it to this page; the
//              checker couldn't read it" (a script-filled page, a PDF too large)
//   osm        OpenStreetMap's beds tag (map data, not checked by the agent), when nothing the agent found passed
//   not found  said plainly, never guessed
// Beds are not a head count: the panel says so. The total says "as reported" only for the reported part. The job
// started when the cascade landed (HospitalAgentStarter), so by this beat it is normally done; while it runs the rows
// fill in as they pass. Never plays audio.
//
// Props: lang 'en' | 'es' · animate (the rows rise in one after another) · max (rows before "Show all")
import { useId, useState } from 'react'
import AgentTrace, { AgentTraceToggle } from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { ErrorBanner } from '../../ui'
import { useHospitalAgent } from './hospitalAgentApi'
import './hospitalAgent.css'

const T = {
  en: {
    heading: 'Hospitals in the dark areas: beds',
    total: 'beds',
    across: (k, n) =>
      `in ${k === n ? `the ${n}` : `${k} of the ${n}`} hospital${n === 1 ? '' : 's'} in areas that lost power (assumed on backup power)`,
    basis: (c, f) =>
      c.osm === 0
        ? 'All as reported, each with its source.'
        : c.reported === 0
          ? 'All from OpenStreetMap map data (not checked).'
          : `${f(c.beds_reported)} as reported, ${f(c.beds_osm)} from OpenStreetMap map data (not checked).`,
    notFound: (n) => `${n} not found`,
    notPeople: 'Beds as reported (licensed or staffed, as each source states them): not a count of the people inside.',
    rule: 'Dark areas come from the synthetic grid model: each hospital’s nearest model substation lost 60 % or more of its load. Real hospitals have their own feeders and generators, so backup power is assumed, not known.',
    working: (n, k) => `The agent is looking up ${n} hospital${n === 1 ? '' : 's'}… ${k} found so far.`,
    starting: 'The agent is starting…',
    none: 'No hospital is in an area that lost power.',
    kinds: { licensed: 'licensed', staffed: 'staffed', beds: 'beds' },
    read: 'read on the page',
    readTip: (q) => `The page says: ${q}`,
    grounded: 'Google tied it to this page; not confirmed on the page',
    osm: 'OpenStreetMap',
    osmNote: 'map data, not checked',
    notFoundRow: 'not found',
    searching: 'searching…',
    queued: 'waiting…',
    all: (n) => `Show all ${n}`,
    fewer: 'Show fewer',
    badge: 'Google Search · checked in code',
    badgeTip: 'Found by a Gemini agent with Google Search; code keeps a figure only when a page Google tied to it gives it as this hospital’s bed count, or, when the page can’t be read, Google tied it to this hospital’s own answer',
    cached: 'checked earlier',
    fallbackWhy: 'OpenStreetMap only',
    watch: 'Watch the agent work',
    suggest: 'Google searches behind these figures',
    credit: 'Hospitals: © OpenStreetMap contributors (ODbL). Beds: each figure’s own source.',
    link: (s) => `${s} (opens in a new tab)`,
  },
  es: {
    heading: 'Hospitales en las zonas sin luz: camas',
    total: 'camas',
    across: (k, n) =>
      `en ${k === n ? `los ${n}` : `${k} de los ${n}`} hospital${n === 1 ? '' : 'es'} en zonas sin luz (se supone que con energía de respaldo)`,
    basis: (c, f) =>
      c.osm === 0
        ? 'Todas según lo publicado, cada una con su fuente.'
        : c.reported === 0
          ? 'Todas de los datos del mapa de OpenStreetMap (sin verificar).'
          : `${f(c.beds_reported)} según lo publicado, ${f(c.beds_osm)} de los datos del mapa de OpenStreetMap (sin verificar).`,
    notFound: (n) => `${n} sin encontrar`,
    notPeople: 'Camas según lo publicado (con licencia o en servicio, como lo dice cada fuente): no es un conteo de las personas adentro.',
    rule: 'Las zonas sin luz vienen del modelo sintético de la red: la subestación del modelo más cercana a cada hospital perdió el 60 % o más de su carga. Los hospitales reales tienen sus propios alimentadores y generadores, así que la energía de respaldo se supone, no se sabe.',
    working: (n, k) => `El agente está buscando ${n} hospital${n === 1 ? '' : 'es'}… ${k} encontrados hasta ahora.`,
    starting: 'El agente está empezando…',
    none: 'Ningún hospital está en una zona sin luz.',
    kinds: { licensed: 'con licencia', staffed: 'en servicio', beds: 'camas' },
    read: 'leído en la página',
    readTip: (q) => `La página dice: ${q}`,
    grounded: 'Google la vinculó a esta página; sin confirmar en la página',
    osm: 'OpenStreetMap',
    osmNote: 'datos del mapa, sin verificar',
    notFoundRow: 'sin encontrar',
    searching: 'buscando…',
    queued: 'en espera…',
    all: (n) => `Ver los ${n}`,
    fewer: 'Ver menos',
    badge: 'Búsqueda de Google · verificado en código',
    badgeTip: 'Hallado por un agente de Gemini con la Búsqueda de Google; el código conserva una cifra solo si una página vinculada por Google la da como las camas de este hospital o, si la página no se puede leer, Google la vinculó a la respuesta de este hospital',
    cached: 'verificado antes',
    fallbackWhy: 'solo OpenStreetMap',
    watch: 'Mira trabajar al agente',
    suggest: 'Búsquedas de Google detrás de estas cifras',
    credit: 'Hospitales: © colaboradores de OpenStreetMap (ODbL). Camas: la fuente de cada cifra.',
    link: (s) => `${s} (se abre en una pestaña nueva)`,
  },
}

const fmt = (n, lang) => Number(n).toLocaleString(lang === 'es' ? 'es-US' : 'en-US')
// the biggest first; the ones still open, then not found, last
const rank = (h) => (h.beds_basis == null ? 1 : h.beds_basis === 'not_found' ? 2 : 0)
const byBeds = (a, b) => rank(a) - rank(b) || (b.beds || 0) - (a.beds || 0) || a.name.localeCompare(b.name)

export default function HospitalsFound({ lang = 'en', animate = false, max = 8 }) {
  const t = T[lang] || T.en
  const a = useHospitalAgent()
  const headId = useId()
  const [all, setAll] = useState(false)
  if (a.status === 'none' || a.status === 'idle') return null
  const d = a.data
  const c = a.totals
  const running = a.status === 'starting' || a.status === 'running'
  const rows = [...a.hospitals].sort(byBeds)
  const shown = all ? rows : rows.slice(0, max)
  const fallback = d?.by === 'fallback'

  let summary = null
  if (a.status === 'starting' && !d) summary = <p className="hfa-status">{t.starting}</p>
  else if (c && c.hospitals === 0) summary = <p className="hfa-status">{t.none}</p>
  else if (c) {
    const found = c.reported + c.osm
    summary = (
      <>
        <p className="hfa-total">
          <span className="hfa-total__n">{fmt(c.beds_total, lang)}</span>{' '}
          <span className="hfa-total__words">
            {t.total} {t.across(found, c.hospitals)}
            {c.not_found > 0 && !running ? ` · ${t.notFound(c.not_found)}` : ''}
          </span>
        </p>
        {!running && found > 0 && <p className="hfa-basis">{t.basis(c, (n) => fmt(n, lang))}</p>}
        {running && (
          <p className="hfa-status" role="status">
            {t.working(c.hospitals, found)}
          </p>
        )}
      </>
    )
  }

  return (
    <section className={`hfa${animate ? ' hfa--anim' : ''}`} aria-labelledby={headId} aria-busy={running}>
      <header className="hfa-head">
        <h3 id={headId} className="hfa-h">
          {t.heading}
        </h3>
        {d && c?.hospitals > 0 && (
          <AiBadge by={fallback ? 'fallback' : 'gemini'} why={fallback ? t.fallbackWhy : undefined} lang={lang} title={fallback ? undefined : t.badgeTip} className="aib--wrap">
            {fallback ? null : d.cached ? `${t.badge} · ${t.cached}` : t.badge}
          </AiBadge>
        )}
      </header>
      <div aria-live="polite">{summary}</div>
      <ErrorBanner error={a.error} onRetry={a.retry} />
      {rows.length > 0 && (
        <ul className="hfa-rows">
          {shown.map((h, i) => (
            <Row key={h.id} h={h} t={t} lang={lang} i={i} />
          ))}
        </ul>
      )}
      {rows.length > max && (
        <button type="button" className="hfa-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? t.fewer : t.all(rows.length)}
        </button>
      )}
      {c?.hospitals > 0 && (
        <>
          <p className="hfa-note">{t.notPeople}</p>
          <p className="hfa-note">{t.rule}</p>
        </>
      )}
      {d?.suggestions?.length > 0 && (
        <div className="hfa-suggest">
          <p className="hfa-suggest__h">{t.suggest}</p>
          {d.suggestions.map((html, i) => (
            <iframe
              key={i}
              className="hfa-suggest__frame"
              title={`${t.suggest} (${i + 1})`}
              sandbox="allow-popups allow-popups-to-escape-sandbox"
              referrerPolicy="no-referrer"
              srcDoc={`<!doctype html><meta name="color-scheme" content="light dark"><base target="_blank"><style>html,body{margin:0;background:transparent}</style>${html}`}
            />
          ))}
        </div>
      )}
      {a.trace.length > 0 &&
        (running ? (
          <AgentTrace trace={a.trace} lang={lang} live heading={t.watch} animate={false} className="hfa-trace" />
        ) : (
          <AgentTraceToggle trace={a.trace} lang={lang} label={t.watch} heading={t.watch} stepMs={300} />
        ))}
      <p className="hfa-credit">{t.credit}</p>
    </section>
  )
}

function Row({ h, t, lang, i }) {
  const basis = h.beds_basis
  const src = h.source
  let where
  if (basis === 'reported') {
    where = (
      <>
        <a href={src.url} target="_blank" rel="noopener noreferrer" aria-label={t.link(src.title)}>
          {src.title}
        </a>
        {' · '}
        {h.page_confirmed ? (
          <span className="hfa-row__ok" title={h.quote ? t.readTip(h.quote) : undefined}>
            {t.read}
          </span>
        ) : (
          <span title={h.note || undefined}>{t.grounded}</span>
        )}
      </>
    )
  } else if (basis === 'osm') {
    where = (
      <>
        <a href={src.url} target="_blank" rel="noopener noreferrer" aria-label={t.link(t.osm)}>
          {t.osm}
        </a>
        {' · '}
        <span title={h.note || undefined}>{t.osmNote}</span>
      </>
    )
  } else if (basis === 'not_found') {
    where = <span title={h.note || undefined}>{t.notFoundRow}</span>
  } else {
    where = <span className="hfa-row__wait">{h.state === 'searching' ? t.searching : t.queued}</span>
  }
  return (
    <li className={`hfa-row hfa-row--${basis || 'open'}`} style={{ '--i': i }}>
      <span className="hfa-row__dot" aria-hidden="true" />
      <span className="hfa-row__name">
        {h.name}
        {h.area && <span className="hfa-row__area"> · {h.area}</span>}
      </span>
      <span className="hfa-row__beds">
        {h.beds ? (
          <>
            {fmt(h.beds, lang)} <span className="hfa-row__kind">{t.kinds[h.beds_kind] || t.kinds.beds}</span>
          </>
        ) : (
          <span className="hfa-row__kind">—</span>
        )}
      </span>
      <span className="hfa-row__src">{where}</span>
    </li>
  )
}
