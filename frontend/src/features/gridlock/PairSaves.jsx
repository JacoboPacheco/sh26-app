import { ErrorBanner } from '../../ui'
import { useGridlock } from './context'
import { LABELS, fmtMonth } from './agreementText'
import { WindowTimeline } from './AgreementDoc'
import Gloss from './Gloss'
import { GLOSS } from './glossary'
import { displayName, fmtDate, fmtKm, fmtMi, fmtMoney, fmtRange, kindText, limitMi, pairDistance, statusText, toneOf, utilityShort } from './format'
import { CONF, nearEndsOnly, planningSide, rangeText, whenText, windowKind } from './plain'

// Step 2 of the guided path, "See what building together saves": the pair folded to five facts a newcomer can read in
// one go (what it could save, where, when, what each company builds, how sure we are), then one button on to step 3.
// Everything else (both projects as filed, the estimate line by line, why the pair ranks, the trace) sits behind one
// fold. Every figure is the pipeline's: the plain draft (GET /api/agreement/<id>?ai=false) carries both projects as
// filed and the savings estimate with its sources.

const T = {
  en: {
    could: 'Building together could save',
    rough: 'Rough estimate from published unit costs, not a quote',
    none: 'Nothing to save as filed',
    noneWhy: 'What these two could share needs their build windows to share months.',
    ifAhead: 'Only if both still have work ahead: the months they shared have passed, as filed.',
    where: 'Where',
    when: 'When',
    builds: 'Who builds what',
    shared: "What's shared",
    sure: 'How sure we are',
    apart: (mi, km) => `${mi} apart at their closest points (${km})`,
    touch: 'The two projects touch: they meet at the same place.',
    cross: 'The two lines cross.',
    station: (n) => `Both filings name work at ${n} substation.`,
    centers: (mi) => `${mi} between their centers (Sperry's method)`,
    nearEnds: (lim) => `Close only at their nearest ends: measured between their centers (Sperry's method), they are more than ${lim} apart.`,
    both: (r, plan) => (plan ? `Build windows overlap ${r}` : `Both building ${r}`),
    past: (r, plan) => (plan ? `Build windows overlapped ${r}: listed for the record` : `Both built ${r}, as filed: listed for the record`),
    planNote: (u) => `${u}'s dates are a filed planning window (start to need date), so this says when both could be building, not that both crews are out then.`,
    apartT: 'Their build windows share no months: building together would mean moving one schedule.',
    open: 'open now',
    ahead: 'still ahead',
    inService: 'in service',
    need: 'needed by',
    placed: (u, c) => `${u}: ${c}`,
    dates: (u, k) => `${u}'s dates: ${k}`,
    next: 'Next: agree on a plan',
    nextWhy: "Each company's AI agent proposes ways to work together; the pipeline checks every figure.",
    more: 'Full detail: both projects as filed, the estimate line by line, why this pair ranks',
    how: 'How the savings are estimated',
    assumptions: 'Assumptions',
    why: (r) => `Why it ranks ${r}`,
    trace: 'Trace this pair: the distance, each end on the map, each PDF page',
    whereFrom: 'Where this came from',
    work: 'Work',
    window: 'Build window',
    status: 'Status',
    cost: 'Cost',
    redacted: 'Redacted in the public filing',
    notGiven: 'Not given',
    publicOnly: "This compares public plans only; it doesn't say whether the utilities already work together.",
    slip: 'Plans move',
  },
  es: {
    could: 'Construir juntos podría ahorrar',
    rough: 'Estimación aproximada con costos unitarios publicados, no una cotización',
    none: 'Nada que ahorrar según lo publicado',
    noneWhy: 'Lo que podrían compartir requiere que sus ventanas de obra compartan meses.',
    ifAhead: 'Solo si a ambos les queda obra: los meses en común ya pasaron, según lo publicado.',
    where: 'Dónde',
    when: 'Cuándo',
    builds: 'Quién construye qué',
    shared: 'Qué se comparte',
    sure: 'Qué tan seguros estamos',
    apart: (mi, km) => `a ${mi} en sus puntos más cercanos (${km})`,
    touch: 'Los dos proyectos se tocan: se encuentran en el mismo lugar.',
    cross: 'Las dos líneas se cruzan.',
    station: (n) => `Ambos documentos mencionan obras en la subestación ${n}.`,
    centers: (mi) => `${mi} entre sus centros (método de Sperry)`,
    nearEnds: (lim) => `Cerca solo en los extremos más próximos: sus centros están a más de ${lim}.`,
    both: (r, plan) => (plan ? `Las ventanas publicadas coinciden ${r}` : `Ambos en obra ${r}`),
    past: (r, plan) => (plan ? `Las ventanas publicadas coincidieron ${r}, según lo publicado` : `Ambos construidos ${r}, según lo publicado`),
    planNote: (u) => `Las fechas de ${u} son una ventana de planificación publicada (del inicio a la fecha de necesidad): indica cuándo podrían estar en obra ambos, no que las dos cuadrillas trabajen entonces.`,
    apartT: 'Sus ventanas de obra no comparten meses: construir juntos exigiría mover un calendario.',
    open: 'abierta ahora',
    ahead: 'aún por delante',
    inService: 'en servicio',
    need: 'necesario para',
    placed: (u, c) => `${u}: ${c}`,
    dates: (u, k) => `Fechas de ${u}: ${k}`,
    next: 'Siguiente: acordar un plan',
    nextWhy: 'El agente de IA de cada empresa propone formas de trabajar juntos; el sistema comprueba cada cifra.',
    more: 'Todo el detalle: ambos proyectos según lo publicado, la estimación partida por partida, por qué ocupa este puesto',
    how: 'Cómo se estima el ahorro',
    assumptions: 'Supuestos',
    why: (r) => `Por qué ocupa el puesto ${r}`,
    trace: 'Rastrear este par: la distancia, cada extremo en el mapa, cada página del PDF',
    whereFrom: 'De dónde sale',
    work: 'Obra',
    window: 'Ventana de obra',
    status: 'Estado',
    cost: 'Costo',
    redacted: 'Tachado en el documento público',
    notGiven: 'No indicado',
    publicOnly: 'Solo compara planes públicos; no dice si las empresas ya trabajan juntas.',
    slip: 'Los planes cambian',
  },
}

const CONF_ES = {
  high: 'ubicado con confianza alta',
  medium: 'ubicado con confianza media',
  low: 'ubicado con confianza baja: compruébelo antes de usarlo',
}

// a project's work in one line (after its voltage): "substation work, $5.4M, in service Dec 2028"
function buildsLine(p, t, lang) {
  const bits = [
    kindText(p, lang),
    p.miles ? `${p.miles} mi` : null,
    p.cost_usd != null ? fmtMoney(p.cost_usd) : p.cost_redacted ? (lang === 'es' ? 'costo tachado' : 'cost redacted') : null,
    p.in_service ? `${p.utility === 'DESC' ? t.inService : t.need} ${fmtDate(p.in_service.slice(0, 7), lang)}` : null,
  ].filter(Boolean)
  const s = bits.join(', ')
  return p.kv?.length ? s : s.charAt(0).toUpperCase() + s.slice(1)
}

export default function PairSaves({ o, base, lang, listed, onNext, onOpenProject, onTrace, onRetry }) {
  const g = useGridlock()
  const t = T[lang] || T.en
  if (base.status === 'loading' || base.status === 'idle') return <SavesSkeleton t={t} />
  if (base.status === 'error') return <ErrorBanner error={new Error("Couldn't read the two filings for this pair. Check the connection and try again.")} onRetry={onRetry} />
  const doc = base.data
  const dr = doc.draft
  const [pa, pb] = doc.overlap?.projects || []
  const sv = dr.savings || {}
  const items = sv.items || []
  const jw = dr.joint_window || {}
  const d = pairDistance(o, 'closest')
  const c = pairDistance(o, 'center')
  const station = o.shared_station
  const near = nearEndsOnly(o, g.params.max_km)
  const rank = o.displayRank ?? o.rank
  const flags = [...new Set([...(o.flags || []), ...(o.warnings || [])])]
  const basis = o.timing_basis && typeof o.timing_basis === 'object' ? o.timing_basis : null
  // what kind of dates each side's build window is (spending | planning | derived), worded by the engine
  const kindOf = (p) => {
    const k = Array.isArray(o.window_kinds) ? o.window_kinds[p.side === 'b' ? 1 : 0] : null
    return (basis && basis[p.side]) || (k && g.ov.window_kinds?.[k]) || windowKind(p)
  }
  // a line rebuilt only on a named section (J1): said under what that company builds
  const partialOf = (p) => (Array.isArray(o.partial) ? o.partial[p.side === 'b' ? 1 : 0] : null)
  const passed = o.group === 'passed' || jw.status === 'past'

  // when, as one sentence
  const apart = lang === 'en' && o.window_gap_days != null ? `${whenText(o).replace(/^./, (c) => c.toUpperCase())}: building together would mean moving one schedule.` : t.apartT
  const plan = planningSide(o)
  const whenLine = jw.overlap && jw.start ? (passed ? t.past(rangeText(jw.start, jw.end, lang), plan) : t.both(rangeText(jw.start, jw.end, lang), plan)) : apart
  // validation warnings that put a side's dates in doubt (money filed after its own in-service date)
  const wflags = (Array.isArray(o.window_flags) ? o.window_flags.flat() : []).filter(Boolean)

  return (
    <div className="bt-saves">
      <section className={`bt-hero${items.length ? '' : ' bt-hero--none'}`} aria-labelledby="bt-hero-h">
        <h3 id="bt-hero-h" className="bt-hero__label">
          {items.length ? t.could : t.none}
        </h3>
        {items.length > 0 ? (
          <>
            <p className="bt-hero__fig">{fmtRange(sv.low, sv.high, sv.unit)}</p>
            <p className="bt-hero__note">{passed ? t.ifAhead : t.rough}</p>
          </>
        ) : (
          <p className="bt-hero__note">{t.noneWhy}</p>
        )}
      </section>

      <dl className="bt-facts">
        <div className="bt-fact">
          <dt>{t.where}</dt>
          <dd>
            <p className="bt-fact__main">
              {station ? t.station(station.name) : d.km == null ? '–' : d.km < 0.05 ? (o.crosses ? t.cross : t.touch) : t.apart(fmtMi(d.mi), fmtKm(d.km))}
            </p>
            {c.km != null && !station && d.km != null && d.km >= 0.05 && (
              <p className="bt-fact__sub">{lang === 'es' ? t.centers(fmtMi(c.mi)) : <Gloss tip={GLOSS.centers}>{t.centers(fmtMi(c.mi))}</Gloss>}</p>
            )}
            {near && <p className="bt-fact__sub bt-fact__warn">{t.nearEnds(limitMi(g.params.max_km))}</p>}
            {o.station_not_shared && lang === 'en' && <p className="bt-fact__sub">{o.station_not_shared}</p>}
          </dd>
        </div>
        <div className="bt-fact">
          <dt>{lang === 'es' ? t.when : <Gloss tip={`${GLOSS.window} ${GLOSS.planning}`}>{t.when}</Gloss>}</dt>
          <dd>
            <p className="bt-fact__main">
              {whenLine}
              {jw.overlap && !passed && jw.status && <span className="bt-fact__tag"> · {jw.status === 'open' ? t.open : t.ahead}</span>}
            </p>
            {wflags.map((f) => (
              <p key={`${f.project}-${f.id}`} className="bt-fact__sub bt-fact__warn">
                {lang === 'es' ? f.text_es || f.text : f.text}
              </p>
            ))}
            {plan && jw.overlap && <p className="bt-fact__sub">{t.planNote(utilityShort(o.window_kinds[0] === 'planning' ? o.a_utility : o.b_utility))}</p>}
            <WindowTimeline jw={jw} parties={dr.parties} t={LABELS[lang] || LABELS.en} />
          </dd>
        </div>
        <div className="bt-fact">
          <dt>{t.builds}</dt>
          <dd>
            {[pa, pb].filter(Boolean).map((p) => (
              <p key={p.id} className="bt-builds">
                <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
                <span>
                  <strong>{utilityShort(p.utility)}</strong> {displayName(p.name) || p.display_name}
                  {partialOf(p)?.text && <span className="bt-builds__part">{partialOf(p).text}</span>}
                  <span className="bt-builds__what">
                    {p.kv?.length > 0 && (
                      <>
                        <Gloss tip={GLOSS.kv}>{`${p.kv.join('/')} kV`}</Gloss>{' '}
                      </>
                    )}
                    {buildsLine(p, t, lang)}
                  </span>
                </span>
              </p>
            ))}
          </dd>
        </div>
        {items.length > 0 && (
          <div className="bt-fact">
            <dt>{t.shared}</dt>
            <dd>
              <ul className="bt-shared">
                {items.map((it) => (
                  <li key={it.id}>
                    <span>{it.label}</span>
                    <span className="bt-shared__fig">{fmtRange(it.low, it.high, it.unit)}</span>
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}
        <div className="bt-fact">
          <dt>{lang === 'es' ? t.sure : <Gloss tip={GLOSS.confidence}>{t.sure}</Gloss>}</dt>
          <dd>
            <ul className="bt-sure">
              {[pa, pb].filter(Boolean).map((p) => (
                <li key={`c-${p.id}`}>{t.placed(utilityShort(p.utility), (lang === 'es' ? CONF_ES : CONF)[p.confidence] || p.confidence || '–')}</li>
              ))}
              {[pa, pb].filter(Boolean).map((p) => (
                <li key={`w-${p.id}`}>{t.dates(utilityShort(p.utility), kindOf(p))}</li>
              ))}
              {flags.map((f) => (
                <li key={f} className="bt-sure__flag">
                  {f}
                </li>
              ))}
              {g.ov.slip?.text && (
                <li className="bt-sure__base">
                  <strong>{t.slip}:</strong> {g.ov.slip.text}
                </li>
              )}
            </ul>
          </dd>
        </div>
      </dl>

      <div className="bt-next">
        <button type="button" className="bt-cta" onClick={onNext}>
          {t.next}
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6 3.5L10.5 8 6 12.5" />
          </svg>
        </button>
        <p className="bt-next__why">{t.nextWhy}</p>
      </div>

      <details className="bt-more">
        <summary>{t.more}</summary>
        <div className="bt-more__body">
          <div className="gl-pair2">
            {pa && <ProjectBox p={pa} t={t} lang={lang} onOpen={onOpenProject} />}
            <div className="gl-pair2__gap" aria-hidden="true">
              <span>{d.km == null ? '–' : d.km < 0.05 ? (o.crosses ? '×' : '•') : fmtMi(d.mi)}</span>
            </div>
            {pb && <ProjectBox p={pb} t={t} lang={lang} onOpen={onOpenProject} />}
          </div>
          {items.length > 0 && (
            <>
              <h4 className="bt-more__h">{t.how}</h4>
              <ul className="gl-est2">
                {items.map((it) => {
                  const src = (sv.sources || []).find((x) => x.title === it.source)
                  return (
                    <li key={it.id}>
                      <div className="gl-est2__line">
                        <span>{it.label}</span>
                        <strong>{fmtRange(it.low, it.high, it.unit)}</strong>
                      </div>
                      <p className="gl-fine">
                        {it.basis}.{' '}
                        {src?.url ? (
                          <a href={src.url} target="_blank" rel="noreferrer">
                            {it.source}
                          </a>
                        ) : (
                          it.source
                        )}
                      </p>
                    </li>
                  )
                })}
              </ul>
            </>
          )}
          {sv.assumptions?.length > 0 && (
            <>
              <h4 className="bt-more__h">{t.assumptions}</h4>
              <ul className="gl-fold__list">
                {sv.assumptions.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </>
          )}
          {o.reasons?.length > 0 && listed && (
            <>
              <h4 className="bt-more__h">{t.why(rank)}</h4>
              <ul className="gl-fold__list">
                {o.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </>
          )}
          <button type="button" className="gl-tracebtn" onClick={(e) => onTrace(e.currentTarget)}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M2.5 3.5h4M2.5 8h7M2.5 12.5h11M9 3.5h4.5M12 8h1.5" />
              <circle cx="7.8" cy="3.5" r="1.3" />
              <circle cx="10.8" cy="8" r="1.3" />
            </svg>
            {t.trace}
          </button>
          <p className="gl-fine">{t.publicOnly}</p>
        </div>
      </details>
    </div>
  )
}

function SavesSkeleton({ t }) {
  return (
    <div className="bt-saves" aria-busy="true">
      <section className="bt-hero">
        <p className="bt-hero__label">{t.could}</p>
        <p className="bt-skel bt-skel--fig" />
        <p className="bt-hero__note">{t.rough}</p>
      </section>
      <dl className="bt-facts">
        {[t.where, t.when, t.builds, t.sure].map((l) => (
          <div key={l} className="bt-fact">
            <dt>{l}</dt>
            <dd>
              <p className="bt-skel" />
              <p className="bt-skel bt-skel--short" />
            </dd>
          </div>
        ))}
      </dl>
      <p className="gl-sr" role="status">
        Reading both filings…
      </p>
    </div>
  )
}

function ProjectBox({ p, t, lang, onOpen }) {
  const g = useGridlock()
  const rec = g.byId[p.id]
  const w = p.window || p.build_window_filed
  const work = [p.kv?.length ? `${p.kv.join(' / ')} kV` : null, kindText(p, lang), p.miles ? `${p.miles} mi` : null].filter(Boolean).join(', ')
  return (
    <article className={`gl-pbox gl-pbox--${toneOf(p.utility)}`}>
      <span className="gl-pbox__who">
        <span className={`gl-swatch gl-swatch--${toneOf(p.utility)}`} aria-hidden="true" />
        {p.utility_name}
      </span>
      <h4 className="gl-pbox__name">
        {displayName(p.name) || p.display_name} <span className="gl-pbox__id">{p.id}</span>
      </h4>
      {rec?.edition === '2024-2028' && <span className="gl-pbox__ed">{rec.edition_note}</span>}
      {p.description && <p className="bt-desc">&ldquo;{p.description}&rdquo;</p>}
      <dl className="gl-pbox__facts">
        {work && (
          <div>
            <dt>{t.work}</dt>
            <dd>{work.charAt(0).toUpperCase() + work.slice(1)}</dd>
          </div>
        )}
        <div>
          <dt>{t.window}</dt>
          <dd>
            {w ? `${fmtMonth(w.start, lang)} – ${fmtMonth(w.end, lang)}` : p.in_service ? fmtDate(p.in_service, lang) : '–'}
            <span className="bt-desc__kind"> ({windowKind(p)})</span>
          </dd>
        </div>
        {p.status && (
          <div>
            <dt>{t.status}</dt>
            <dd>{statusText(p.status, lang)}</dd>
          </div>
        )}
        <div>
          <dt>{t.cost}</dt>
          <dd>{p.cost_usd != null ? fmtMoney(p.cost_usd) : p.cost_redacted ? t.redacted : t.notGiven}</dd>
        </div>
      </dl>
      <p className="gl-pbox__links">
        {p.source?.url ? (
          <a href={p.source.url} target="_blank" rel="noreferrer">
            {p.source.label}
          </a>
        ) : (
          <span>{p.source?.label}</span>
        )}
        <button type="button" className="gl-link" onClick={(e) => onOpen(p.id, e.currentTarget)}>
          {t.whereFrom}
        </button>
      </p>
    </article>
  )
}
