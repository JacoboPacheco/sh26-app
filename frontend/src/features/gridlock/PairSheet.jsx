import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ErrorBanner, Loading } from '../../ui'
import { useGridlock } from './context'
import { LABELS, agreementMarkdown, fmtMonth } from './agreementText'
import { DraftStatus, Paper, PrintCopy, WindowTimeline } from './AgreementDoc'
import { useAgreement } from './useAgreement'
import { ProjectCard } from './DetailCard'
import Negotiation from './Negotiation'
import TraceDrawer from './TraceDrawer'
import {
  SAME_STATION,
  TIER_LABEL_ES,
  TIER_SHARE,
  TIER_SHARE_ES,
  displayName,
  fmtDate,
  fmtKm,
  fmtMi,
  fmtMoney,
  fmtRange,
  kindText,
  pairDistance,
  stationSentence,
  statusText,
  toneOf,
  utilityShort,
  whenOf,
} from './format'
import './agreement.css'
import './negotiation.css'

// A pair's sheet: a wide panel over the right of the map (full screen on a phone) with three numbered steps,
// because they are a sequence: (1) the overlap as filed, (2) two AI agents negotiating the terms, (3) the drafted
// agreement (print / download). The header names the pair in plain words and holds the language, Print and
// Download; the step row under it jumps to each step and says where each stands. A project's "Where this came
// from" opens over the steps (Back returns to the pair) so a negotiation in progress is never lost.

const S = {
  en: {
    steps: ['The overlap', 'Two AI agents negotiate', 'The drafted agreement'],
    stepsShort: ['Overlap', 'Negotiate', 'Agreement'],
    title: (a, b, share) => `${a} and ${b} could share ${share}`,
    stationTitle: (a, b, name) => `${a} and ${b} both work at ${name}`,
    stationWhy: 'A substation both utilities work at is where their systems meet, so outages and equipment work there could be planned once.',
    stationMap: (n) => `${n} on OpenStreetMap`,
    // no OpenStreetMap substation carries the filed name: the link is the nearest one, standing in
    stationStandIn: (n) => `The substation standing in for ${n} on OpenStreetMap (none there carries the name)`,
    pairOf: (r, n) => (n ? `Pair ${r} of ${n}` : `Pair ${r}`),
    apart: (d) => `${d} apart`,
    touching: 'The projects touch',
    crossing: 'The projects cross',
    touch: 'touch',
    cross: 'cross',
    outside: 'Outside the current filters',
    close: 'Close the pair',
    back: 'Pairs',
    lang: 'Language',
    print: 'Print',
    printTitle: 'Print or save as PDF: the drafted agreement alone',
    download: 'Download',
    downloadTitle: 'Download the drafted agreement as text (.md)',
    work: 'Work',
    window: 'Build window',
    derived: 'start derived',
    status: 'Status',
    cost: 'Cost',
    redacted: 'Redacted in the public filing',
    notGiven: 'Not given',
    whereFrom: 'Where this came from',
    distance: 'Distance',
    closest: 'at the closest points',
    centers: 'between centers',
    shared: 'Shared build window',
    noShared: 'No shared months',
    savings: 'Could save',
    rough: 'rough estimate',
    how: 'How the savings are estimated',
    assumptions: 'Assumptions',
    sources: 'Sources',
    needs: (x) => `needs ${x}`,
    why: (r) => `Why it ranks ${r}`,
    trace: 'Trace this pair',
    traceWhat: "The score's terms, the distance, each end's map match and each row's PDF page",
    publicOnly: "This compares public plans only; it doesn't say whether the utilities already work together.",
    loading: 'Reading both filings…',
    drafting: 'Drafting from the two filings…',
    st: {
      idle: 'Not run yet',
      running: 'Negotiating…',
      agreed: (r) => `Agreed in round ${r}`,
      none: 'No agreement',
      error: 'Stopped; try again',
      draft: 'Drafting…',
      aiPending: 'Plain draft; Gemini wording it',
      negotiated: 'Uses the agreed terms',
      ready: 'Ready to print',
    },
  },
  es: {
    steps: ['La coincidencia', 'Dos agentes de IA negocian', 'El acuerdo redactado'],
    stepsShort: ['Coincidencia', 'Negociación', 'Acuerdo'],
    title: (a, b, share) => `${a} y ${b} podrían compartir ${share}`,
    stationTitle: (a, b, name) => `${a} y ${b} trabajan en la misma subestación: ${name}`,
    stationWhy: 'Una subestación en la que trabajan las dos empresas es donde se unen sus sistemas: los cortes y las obras allí podrían planificarse una sola vez.',
    stationMap: (n) => `${n} en OpenStreetMap`,
    stationStandIn: (n) => `La subestación que representa a ${n} en OpenStreetMap (ninguna allí lleva ese nombre)`,
    pairOf: (r, n) => (n ? `Par ${r} de ${n}` : `Par ${r}`),
    apart: (d) => `a ${d}`,
    touching: 'Los proyectos se tocan',
    crossing: 'Los proyectos se cruzan',
    touch: 'se tocan',
    cross: 'se cruzan',
    outside: 'Fuera de los filtros actuales',
    close: 'Cerrar el par',
    back: 'Pares',
    lang: 'Idioma',
    print: 'Imprimir',
    printTitle: 'Imprimir o guardar como PDF: solo el acuerdo redactado',
    download: 'Descargar',
    downloadTitle: 'Descargar el acuerdo redactado como texto (.md)',
    work: 'Obra',
    window: 'Ventana de obra',
    derived: 'inicio derivado',
    status: 'Estado',
    cost: 'Costo',
    redacted: 'Tachado en el documento público',
    notGiven: 'No indicado',
    whereFrom: 'De dónde sale',
    distance: 'Distancia',
    closest: 'entre los puntos más cercanos',
    centers: 'entre centros',
    shared: 'Ventana de obra compartida',
    noShared: 'Sin meses en común',
    savings: 'Podría ahorrar',
    rough: 'estimación aproximada',
    how: 'Cómo se estima el ahorro',
    assumptions: 'Supuestos',
    sources: 'Fuentes',
    needs: (x) => `requiere ${x}`,
    why: (r) => `Por qué ocupa el puesto ${r}`,
    trace: 'Rastrear este par',
    traceWhat: 'Los términos de la puntuación, la distancia, la ubicación de cada extremo y la página del PDF de cada fila',
    publicOnly: 'Solo compara planes públicos; no dice si las empresas ya trabajan juntas.',
    loading: 'Leyendo los dos documentos…',
    drafting: 'Redactando a partir de los dos documentos…',
    st: {
      idle: 'Aún sin ejecutar',
      running: 'Negociando…',
      agreed: (r) => `Acuerdo en la ronda ${r}`,
      none: 'Sin acuerdo',
      error: 'Detenida; reintente',
      draft: 'Redactando…',
      aiPending: 'Borrador simple; Gemini redactando',
      negotiated: 'Usa los términos acordados',
      ready: 'Listo para imprimir',
    },
  },
}

function useReduced() {
  const [r] = useState(() => {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches
    } catch {
      return false
    }
  })
  return r
}

// a value once it has stopped changing for `ms` (the first value at once)
function useSettled(value, ms) {
  const [v, setV] = useState(value)
  useEffect(() => {
    if (Object.is(v, value)) return undefined
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms, v])
  return v
}

export default function PairSheet() {
  const g = useGridlock()
  const { draft, closeDraft, client, sel, setCover, back } = g
  // the Build window slider moves the list at once; the sheet asks for its drafts only once the value has settled
  // (a sweep across the slider used to send two drafts per step, one of them Gemini's, and hit the rate limit)
  const months = useSettled(g.params.window_months, 450)
  const reduced = useReduced()
  const [lang, setLang] = useState('en')
  const [tries, setTries] = useState(0)
  const s = S[lang]
  const live = g.overlaps.find((x) => x.id === draft.id)
  const o = live || draft.overlap
  // a pair the current filters leave out has no place in the list: say so rather than show its old rank
  const listed = !!live || g.ov.status === 'loading' || g.ov.status === 'refreshing'
  const rank = o.displayRank ?? o.rank

  // the draft's versions: the plain one (step 1 reads the overlap from it), the negotiated plain one once terms are
  // applied, and Gemini's wording of whichever is current
  const caseKey = `${draft.id}@${months}`
  const [negSel, setNegSel] = useState({ for: null, key: null })
  const negKey = negSel.for === caseKey ? negSel.key : null
  const base = useAgreement(client, draft.id, { months, lang, ai: false, tries })
  const negPlain = useAgreement(client, negKey ? draft.id : null, { months, lang, ai: false, negotiated: negKey, tries })
  // Gemini's wording: asked for only once the pair and language have stayed put for a moment (kept answers show at once)
  const aiDoc = useAgreement(client, draft.id, { months, lang, ai: true, negotiated: negKey, tries, defer: 600 })
  const plain = negKey ? negPlain : base
  const doc = aiDoc.status === 'ready' ? aiDoc.data : plain.status === 'ready' ? plain.data : null
  const aiPending = aiDoc.status === 'loading'
  const failed = plain.status === 'error' && aiDoc.status === 'error'
  const t = LABELS[doc?.lang === 'es' ? 'es' : lang]
  const [neg, setNeg] = useState({ phase: 'idle' })

  // how much of the map the sheet covers (the map frames the pair in what is left); 0 when full screen
  const ref = useRef(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const mq = window.matchMedia('(min-width: 900px)')
    const measure = () => {
      const w = Math.round(el.getBoundingClientRect().width)
      if (w) setCover(mq.matches ? w : 0) // 0 wide = hidden (printing): keep the last measure
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    mq.addEventListener?.('change', measure)
    return () => {
      ro.disconnect()
      mq.removeEventListener?.('change', measure)
      setCover(0)
    }
  }, [setCover])

  // focus: the title when a pair opens; back to its row in the list when the sheet closes
  const titleRef = useRef(null)
  const lastId = useRef(draft.id)
  const bodyRef = useRef(null)
  // the step in view (per pair: a new pair starts at step 1)
  const [spy, setSpy] = useState({ id: draft.id, n: 1 })
  const active = spy.id === draft.id ? spy.n : 1
  useEffect(() => {
    lastId.current = draft.id
    titleRef.current?.focus({ preventScroll: true })
    bodyRef.current?.scrollTo({ top: 0 })
  }, [draft.id])
  useEffect(
    () => () => {
      const row = document.querySelector(`[data-pair="${CSS.escape(lastId.current)}"]`)
      if (row && getComputedStyle(row).visibility !== 'hidden') row.focus({ preventScroll: false })
    },
    [],
  )

  const projectOpen = sel?.kind === 'project'
  // "Trace this pair" opens over the steps like a project's card (per pair: a new pair starts closed)
  const [traceFor, setTraceFor] = useState(null)
  const traceOpen = traceFor === draft.id && !projectOpen
  const traceOpener = useRef(null)
  const openTrace = useCallback(
    (el) => {
      traceOpener.current = el || null
      setTraceFor(draft.id)
    },
    [draft.id],
  )
  const closeTrace = useCallback(() => {
    setTraceFor(null)
    const el = traceOpener.current
    traceOpener.current = null
    requestAnimationFrame(() => (el?.isConnected ? el.focus({ preventScroll: false }) : titleRef.current?.focus({ preventScroll: true })))
  }, [])
  const covered = projectOpen || traceOpen
  // "Where this came from" opens over the steps: its heading takes focus (ProjectCard autoFocus); Back returns focus
  // to the link that opened it (else the sheet's title)
  const opener = useRef(null)
  const wasOpen = useRef(false)
  useEffect(() => {
    if (projectOpen) {
      wasOpen.current = true
      return
    }
    if (!wasOpen.current) return
    wasOpen.current = false
    const el = opener.current
    opener.current = null
    if (el?.isConnected) el.focus({ preventScroll: false })
    else titleRef.current?.focus({ preventScroll: true })
  }, [projectOpen])
  const openProjectFrom = g.openProject
  const openProject = useCallback(
    (id, el) => {
      opener.current = el || null
      openProjectFrom(id)
    },
    [openProjectFrom],
  )
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      if (projectOpen) back()
      else if (traceOpen) closeTrace()
      else closeDraft()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [projectOpen, traceOpen, back, closeDraft, closeTrace])

  // print = the document alone (agreement.css hides the app while body has the print copy / .gl-printing)
  useEffect(() => {
    const on = () => document.body.classList.add('gl-printing')
    const off = () => document.body.classList.remove('gl-printing')
    window.addEventListener('beforeprint', on)
    window.addEventListener('afterprint', off)
    return () => {
      window.removeEventListener('beforeprint', on)
      window.removeEventListener('afterprint', off)
      off()
    }
  }, [])
  const print = useCallback(() => {
    document.body.classList.add('gl-printing')
    window.print()
    setTimeout(() => document.body.classList.remove('gl-printing'), 500)
  }, [])
  const download = useCallback(() => {
    if (!doc) return
    const blob = new Blob([agreementMarkdown(doc, doc.lang)], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `draft-coordination-proposal-${doc.overlap_id.replace(/[^\w-]+/g, '_')}.md`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
  }, [doc])

  // "Use these terms in the draft": the draft is re-asked with the negotiation's terms, then step 3 comes into view
  const jumpToDraft = useRef(false)
  const applyTerms = useCallback(
    (k) => {
      jumpToDraft.current = !!k
      setNegSel({ for: caseKey, key: k })
    },
    [caseKey],
  )
  useEffect(() => {
    if (!jumpToDraft.current || !doc?.draft?.negotiated) return
    jumpToDraft.current = false
    requestAnimationFrame(() => scrollBodyTo(bodyRef.current, bodyRef.current?.querySelector('[data-step="3"]'), reduced))
  }, [doc, reduced])

  // the step row follows the scroll
  const onScroll = useCallback(() => {
    const b = bodyRef.current
    if (!b) return
    const top = b.getBoundingClientRect().top
    let cur = 1
    for (const el of b.querySelectorAll('[data-step]')) if (el.getBoundingClientRect().top - top < 140) cur = Number(el.dataset.step)
    setSpy({ id: lastId.current, n: cur })
  }, [])
  const goTo = (n) => {
    const el = bodyRef.current?.querySelector(`[data-step="${n}"]`)
    if (!el) return
    scrollBodyTo(bodyRef.current, el, reduced)
    el.querySelector('h3')?.focus({ preventScroll: true })
  }

  const a = g.byId[o.a]
  const b = g.byId[o.b]
  const aShort = utilityShort(a?.utility)
  const bShort = utilityShort(b?.utility)
  const share = (lang === 'es' ? TIER_SHARE_ES : TIER_SHARE)[o.tier]
  const station = o.shared_station
  // a shared substation is the headline: the title names it, the sub line says it is the same one in both filings
  const title = station
    ? s.stationTitle(aShort, bShort, station.name)
    : s.title(aShort, bShort, lang === 'es' ? share : (share || 'work').toLowerCase())
  const d = pairDistance(o, g.params.method)
  const distText = station
    ? SAME_STATION[lang].sub
    : d.km == null
      ? ''
      : d.km < 0.05
        ? o.crosses
          ? s.crossing
          : s.touching
        : s.apart(`${fmtMi(d.mi)} (${fmtKm(d.km)})`)
  const when = whenOf(o, lang)
  const status = [
    when.short,
    neg.phase === 'idle'
      ? s.st.idle
      : neg.phase === 'running'
        ? s.st.running
        : neg.phase === 'error'
          ? s.st.error
          : neg.agreed
            ? s.st.agreed(neg.round)
            : s.st.none,
    !doc ? s.st.draft : aiPending ? s.st.aiPending : doc.draft?.negotiated ? s.st.negotiated : s.st.ready,
  ]

  return (
    <aside ref={ref} className="gl gl-sheet" aria-labelledby="gl-sheet-h">
      <header className="gl-sheet__head">
        <div className="gl-sheet__top">
          <button type="button" className="gl-back gl-sheet__back" onClick={closeDraft}>
            <span aria-hidden="true">‹</span> {s.back}
          </button>
          <div className="gl-sheet__titles">
            <h2 id="gl-sheet-h" ref={titleRef} tabIndex={-1}>
              {title}
            </h2>
            <p className="gl-sheet__sub">
              {listed ? s.pairOf(rank, g.ov.flagged) : s.outside}
              {distText ? `, ${distText.charAt(0).toLowerCase()}${distText.slice(1)}` : ''}
            </p>
          </div>
          <button type="button" className="gl-close" onClick={closeDraft} aria-label={s.close} title={s.close}>
            ×
          </button>
        </div>
        <div className="gl-sheet__tools">
          <div className="gl-lang" role="group" aria-label={s.lang}>
            {[
              ['en', 'EN', 'English'],
              ['es', 'ES', 'Español'],
            ].map(([id, label, full]) => (
              <button key={id} type="button" aria-pressed={lang === id} className={lang === id ? 'is-on' : ''} onClick={() => setLang(id)} title={full}>
                <span aria-hidden="true">{label}</span>
                <span className="gl-sr">{full}</span>
              </button>
            ))}
          </div>
          <button type="button" className="gl-tool" onClick={print} disabled={!doc} title={s.printTitle}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M4.5 6V2.5h7V6M4.5 11.5h-2v-5h11v5h-2M4.5 9.5h7v4h-7z" />
            </svg>
            {s.print}
          </button>
          <button type="button" className="gl-tool" onClick={download} disabled={!doc} title={s.downloadTitle}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10" />
            </svg>
            {s.download}
          </button>
        </div>
        {!covered && (
          <nav className="gl-steps" aria-label={lang === 'es' ? 'Pasos' : 'Steps'}>
            {s.steps.map((label, i) => (
              <button key={label} type="button" className={active === i + 1 ? 'is-on' : ''} aria-current={active === i + 1 ? 'step' : undefined} onClick={() => goTo(i + 1)}>
                <span className="gl-steps__n" aria-hidden="true">
                  {i + 1}
                </span>
                <span className="gl-steps__t">
                  <span className="gl-steps__full">{label}</span>
                  <span className="gl-steps__short" aria-hidden="true">
                    {s.stepsShort[i]}
                  </span>
                </span>
                <span className={`gl-steps__s${i === 1 && neg.agreed ? ' is-ok' : ''}`}>{status[i]}</span>
              </button>
            ))}
          </nav>
        )}
      </header>

      <div className="gl-sheet__main">
        <div className="gl-sheet__body" ref={bodyRef} onScroll={onScroll} inert={covered || undefined}>
          <Step
            n={1}
            title={s.steps[0]}
            aside={
              station && (
                <span className="gl-stationtag" title={station.how || undefined}>
                  {SAME_STATION[lang].tag(station.name)}
                </span>
              )
            }
          >
            <OverlapStep
              o={o}
              base={base}
              s={s}
              lang={lang}
              listed={listed}
              onOpenProject={openProject}
              onTrace={openTrace}
              onRetry={() => setTries((n) => n + 1)}
            />
          </Step>
          <Step n={2} title={s.steps[1]}>
            {client && (
              <Negotiation
                key={caseKey}
                client={client}
                draftId={draft.id}
                months={months}
                lang={lang}
                parties={base.data?.draft?.parties}
                names={{ a: { short: aShort, code: a?.utility }, b: { short: bShort, code: b?.utility } }}
                used={negKey}
                onUse={applyTerms}
                onStatus={setNeg}
              />
            )}
          </Step>
          <Step
            n={3}
            title={s.steps[2]}
            aside={doc && <DraftStatus doc={doc} pending={aiPending} aiError={aiDoc.status === 'error' ? aiDoc.error : null} t={t} onRetry={() => setTries((n) => n + 1)} />}
          >
            {!doc && !failed && <Loading label={s.drafting} />}
            {failed && <ErrorBanner error={plain.error} onRetry={() => setTries((n) => n + 1)} />}
            {doc && (
              <div className="gl-desk">
                <Paper doc={doc} t={t} onDropNeg={() => applyTerms(null)} />
              </div>
            )}
          </Step>
        </div>
        {projectOpen && (
          <div className="gl-sheet__layer">
            <ProjectCard sel={sel} onBack={back} backLabel={lang === 'es' ? `par ${rank}` : `pair ${rank}`} autoFocus />
          </div>
        )}
        {traceOpen && (
          <div className="gl-sheet__layer gl-sheet__layer--trace">
            <TraceDrawer id={draft.id} rank={listed ? rank : null} lang={lang} onBack={closeTrace} />
          </div>
        )}
      </div>
      {doc && <PrintCopy doc={doc} t={t} />}
    </aside>
  )
}

// scroll the sheet's own body (never the page) so `el` sits at its top
function scrollBodyTo(body, el, reduced) {
  if (!body || !el) return
  const top = body.scrollTop + el.getBoundingClientRect().top - body.getBoundingClientRect().top
  body.scrollTo({ top: Math.max(0, top - 2), behavior: reduced ? 'auto' : 'smooth' })
}

function Step({ n, title, aside, children }) {
  return (
    <section className="gl-step" data-step={n} aria-labelledby={`gl-step-${n}`}>
      <div className="gl-step__head">
        <span className="gl-step__n" aria-hidden="true">
          {n}
        </span>
        <h3 id={`gl-step-${n}`} tabIndex={-1}>
          {title}
        </h3>
        {aside && <div className="gl-step__aside">{aside}</div>}
      </div>
      {children}
    </section>
  )
}

// Step 1: both projects as filed, side by side with the distance between them; the shared window against today;
// what building together could save and why; the ranking's reasons folded away.
function OverlapStep({ o, base, s, lang, listed, onOpenProject, onTrace, onRetry }) {
  const g = useGridlock()
  if (base.status === 'loading') return <Loading label={s.loading} />
  if (base.status === 'error') return <ErrorBanner error={base.error} onRetry={onRetry} />
  const doc = base.data
  const dr = doc.draft
  const [pa, pb] = doc.overlap?.projects || []
  const d = pairDistance(o, g.params.method)
  const when = whenOf(o, lang)
  const jw = dr.joint_window
  const sv = dr.savings
  const rank = o.displayRank ?? o.rank
  // the dashed gap between the two boxes is narrow: a short word there, the full sentence under "Distance"
  const bridge = d.km == null ? '–' : d.km < 0.05 ? (o.crosses ? s.cross : s.touch) : fmtMi(d.mi)
  const shareLabels = (sv.items || []).map((it) => it.label)
  const station = o.shared_station
  const said = stationSentence(o, lang)
  return (
    <div className="gl-ov">
      {station && said && (
        <div className="gl-ovstation">
          <p className="gl-ovstation__line">
            <strong>{said.lead}</strong>
            {said.rest}.
          </p>
          <p className="gl-ovstation__why">
            {s.stationWhy}{' '}
            {station.osm_url && (
              <a href={station.osm_url} target="_blank" rel="noreferrer">
                {station.stand_in ? s.stationStandIn(station.name) : s.stationMap(station.osm_name || station.name)}
              </a>
            )}
          </p>
        </div>
      )}
      <div className="gl-pair2">
        {pa && <ProjectBox p={pa} s={s} lang={lang} onOpen={onOpenProject} />}
        <div className="gl-pair2__gap" aria-hidden="true">
          <span>{bridge}</span>
        </div>
        {pb && <ProjectBox p={pb} s={s} lang={lang} onOpen={onOpenProject} />}
      </div>

      <dl className="gl-ovfacts">
        <div>
          <dt>{s.distance}</dt>
          <dd className="gl-ovfacts__big">
            {station ? SAME_STATION[lang].label : d.km == null ? '–' : d.km < 0.05 ? (o.crosses ? s.crossing : s.touching) : fmtMi(d.mi)}
          </dd>
          <dd>
            {station
              ? station.name
              : d.km != null && d.km >= 0.05
                ? `${fmtKm(d.km)} ${g.params.method === 'center' ? s.centers : s.closest}`
                : lang === 'es'
                  ? TIER_LABEL_ES[o.tier] || o.tier_label
                  : o.tier_label}
          </dd>
        </div>
        <div>
          <dt>{s.shared}</dt>
          <dd className="gl-ovfacts__big">{jw.overlap && jw.start ? `${fmtMonth(jw.start, lang)} – ${fmtMonth(jw.end, lang)}` : s.noShared}</dd>
          <dd>
            {when.months ? `${when.months}, ` : ''}
            <span className={`gl-when gl-when--${when.tone}`}>{when.short}</span>
          </dd>
        </div>
        <div>
          <dt>{s.savings}</dt>
          <dd className="gl-ovfacts__big gl-ovfacts__save">{fmtRange(sv.low, sv.high, sv.unit)}</dd>
          <dd>{shareLabels.length ? shareLabels.join('; ') : s.rough}</dd>
        </div>
      </dl>

      <div className="gl-tracebar">
        <button type="button" className="gl-tracebtn" onClick={(e) => onTrace(e.currentTarget)}>
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M2.5 3.5h4M2.5 8h7M2.5 12.5h11M9 3.5h4.5M12 8h1.5" />
            <circle cx="7.8" cy="3.5" r="1.3" />
            <circle cx="10.8" cy="8" r="1.3" />
          </svg>
          {s.trace}
        </button>
        <span className="gl-fine">{s.traceWhat}</span>
      </div>

      <WindowTimeline jw={jw} parties={dr.parties} t={LABELS[lang]} />

      <details className="gl-fold">
        <summary>{s.how}</summary>
        <ul className="gl-est2">
          {(sv.items || []).map((it) => {
            const src = (sv.sources || []).find((x) => x.title === it.source)
            return (
              <li key={it.id}>
                <div className="gl-est2__line">
                  <span>{it.label}</span>
                  <strong>{fmtRange(it.low, it.high, it.unit)}</strong>
                </div>
                <p className="gl-fine">
                  {it.basis}
                  {it.needs ? `; ${s.needs(it.needs)}` : ''}.{' '}
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
        {sv.assumptions?.length > 0 && (
          <>
            <h5>{s.assumptions}</h5>
            <ul className="gl-fold__list">
              {sv.assumptions.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </>
        )}
      </details>

      {o.reasons?.length > 0 && listed && (
        <details className="gl-fold">
          <summary>{s.why(rank)}</summary>
          <ul className="gl-fold__list">
            {o.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </details>
      )}
      <p className="gl-fine">{s.publicOnly}</p>
    </div>
  )
}

function ProjectBox({ p, s, lang, onOpen }) {
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
      <dl className="gl-pbox__facts">
        {work && (
          <div>
            <dt>{s.work}</dt>
            <dd>{work.charAt(0).toUpperCase() + work.slice(1)}</dd>
          </div>
        )}
        <div>
          <dt>{s.window}</dt>
          <dd>
            {w ? `${fmtMonth(w.start, lang)} – ${fmtMonth(w.end, lang)}` : p.in_service ? fmtDate(p.in_service, lang) : '–'}
            {w?.assumed ? ` (${s.derived})` : ''}
          </dd>
        </div>
        {p.status && (
          <div>
            <dt>{s.status}</dt>
            <dd>{statusText(p.status, lang)}</dd>
          </div>
        )}
        <div>
          <dt>{s.cost}</dt>
          <dd>{p.cost_usd != null ? fmtMoney(p.cost_usd) : p.cost_redacted ? s.redacted : s.notGiven}</dd>
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
          {s.whereFrom}
        </button>
      </p>
    </article>
  )
}
