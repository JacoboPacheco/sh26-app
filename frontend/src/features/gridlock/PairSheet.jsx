import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ErrorBanner, Loading } from '../../ui'
import { useGridlock } from './context'
import { LABELS, agreementMarkdown } from './agreementText'
import { DraftStatus, Paper, PrintCopy } from './AgreementDoc'
import { useAgreement } from './useAgreement'
import { usePlans, useElapsed } from './usePlans'
import { downloadUrl } from './gridlockApi'
import { ProjectCard } from './DetailCard'
import PairSaves from './PairSaves'
import PlanCards, { PlansLoading } from './PlanCards'
import TraceDrawer from './TraceDrawer'
import { utilityShort } from './format'
import { whenText, whereText } from './plain'
import './agreement.css'
import './plans.css'

// A pair's sheet: steps 2 and 3 of the guided path, over the right of the map (full screen on a phone). Step 1, "Find
// an overlap", is the ranked list in the rail; picking a pair opens this at step 2, "See what building together
// saves" (five facts, PairSaves.jsx); its button leads to step 3, "Agree on a plan": the companies' agents' plans
// (PlanCards.jsx), and choosing one drafts the agreement from that plan's terms (Print / Download / Add to calendar).
// The step row in the header says where you are and moves between steps; the address follows
// (#/plans/pair/<id> and #/plans/pair/<id>/plans). A project's "Where this came from" and "Trace this pair" open over
// the steps (Back returns to the pair).

const S = {
  en: {
    steps: ['Find an overlap', 'See what it saves', 'Agree on a plan'],
    title: (a, b) => `${a} and ${b} could build together`,
    stationTitle: (a, b, n) => `${a} and ${b} both plan work at ${n}`,
    pairOf: (r, n) => (n ? `Pair ${r} of ${n}` : `Pair ${r}`),
    outside: 'Outside the current filters',
    close: 'Close the pair',
    back: 'All pairs',
    lang: 'Language',
    print: 'Print',
    printTitle: 'Print or save as PDF: the drafted agreement alone',
    download: 'Download',
    downloadTitle: 'Download the drafted agreement as text (.md)',
    ics: 'Add to calendar',
    icsTitle: 'The shared build window as a calendar file (.ics)',
    draftH: 'Your draft agreement',
    draftFrom: (x) => `Drafted from "${x}". A draft for discussion, not an agreement between, or endorsed by, either utility.`,
    drafting: 'Drafting from the chosen plan…',
    pick: 'Choose a plan above to draft the agreement from it.',
    back2: 'Back to what it saves',
  },
  es: {
    steps: ['Encontrar una coincidencia', 'Ver cuánto ahorra', 'Acordar un plan'],
    title: (a, b) => `${a} y ${b} podrían construir juntos`,
    stationTitle: (a, b, n) => `${a} y ${b} planean obras en ${n}`,
    pairOf: (r, n) => (n ? `Par ${r} de ${n}` : `Par ${r}`),
    outside: 'Fuera de los filtros actuales',
    close: 'Cerrar el par',
    back: 'Todos los pares',
    lang: 'Idioma',
    print: 'Imprimir',
    printTitle: 'Imprimir o guardar como PDF: solo el acuerdo redactado',
    download: 'Descargar',
    downloadTitle: 'Descargar el acuerdo redactado como texto (.md)',
    ics: 'Añadir al calendario',
    icsTitle: 'La ventana de obra compartida como archivo de calendario (.ics, en inglés)',
    draftH: 'Su borrador de acuerdo',
    draftFrom: (x) => `Redactado a partir de «${x}». Un borrador para conversar, no un acuerdo entre las empresas ni respaldado por ninguna de ellas.`,
    drafting: 'Redactando a partir del plan elegido…',
    pick: 'Elija un plan arriba para redactar el acuerdo.',
    back2: 'Volver a cuánto ahorra',
  },
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
  const months = useSettled(g.params.window_months, 450)
  const [lang, setLang] = useState('en')
  const [tries, setTries] = useState(0)
  const [planTries, setPlanTries] = useState(0)
  const s = S[lang]
  const step = g.pairStep === 'plans' ? 3 : 2
  const live = g.overlaps.find((x) => x.id === draft.id)
  const o = live || draft.overlap
  const listed = !!live || g.ov.status === 'loading' || g.ov.status === 'refreshing'
  const rank = o.displayRank ?? o.rank

  // the pair's plain draft (step 2 reads both projects and the estimate from it)
  const base = useAgreement(client, draft.id, { months, lang, ai: false, tries })
  // the plans (asked only once step 3 opens) and the plan the viewer chose, per pair and window setting
  const caseKey = `${draft.id}@${months}`
  const plans = usePlans(client, step === 3 ? draft.id : null, { lang, months, tries: planTries })
  const loadingPlans = step === 3 && plans.status === 'loading'
  const elapsed = useElapsed(loadingPlans)
  const [chosen, setChosen] = useState({ for: null, plan: null })
  const planId = chosen.for === caseKey ? chosen.plan : null
  const planPlain = useAgreement(client, planId ? draft.id : null, { months, lang, ai: false, plan: planId, tries })
  const planAi = useAgreement(client, planId ? draft.id : null, { months, lang, ai: true, plan: planId, tries, defer: 300 })
  const doc = planAi.status === 'ready' ? planAi.data : planPlain.status === 'ready' ? planPlain.data : null
  const aiPending = planAi.status === 'loading'
  const draftFailed = planPlain.status === 'error' && planAi.status === 'error'
  const t = LABELS[doc?.lang === 'es' ? 'es' : lang]
  const chosenPlan = planId && plans.status === 'ready' ? (plans.data?.plans || []).find((p) => p.id === planId) : null

  // how much of the map the sheet covers (the map frames the pair in what is left); 0 when full screen
  const ref = useRef(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const mq = window.matchMedia('(min-width: 900px)')
    const measure = () => {
      const w = Math.round(el.getBoundingClientRect().width)
      if (w) setCover(mq.matches ? w : 0)
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

  // focus: the title when a pair opens or the step changes; back to its row in the list when the sheet closes
  const titleRef = useRef(null)
  const stepRef = useRef(null)
  const lastId = useRef(draft.id)
  const bodyRef = useRef(null)
  useEffect(() => {
    lastId.current = draft.id
    titleRef.current?.focus({ preventScroll: true })
    bodyRef.current?.scrollTo({ top: 0 })
  }, [draft.id])
  const firstStep = useRef(true)
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false
      return
    }
    bodyRef.current?.scrollTo({ top: 0 })
    stepRef.current?.focus({ preventScroll: true })
  }, [step])
  useEffect(
    () => () => {
      const row = document.querySelector(`[data-pair="${CSS.escape(lastId.current)}"]`)
      if (row && getComputedStyle(row).visibility !== 'hidden') row.focus({ preventScroll: false })
    },
    [],
  )

  const projectOpen = sel?.kind === 'project'
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

  // choosing a plan drafts the agreement from it; the draft comes into view under the cards
  const draftRef = useRef(null)
  const choose = useCallback((id) => setChosen({ for: caseKey, plan: id }), [caseKey])
  // once the chosen plan's draft is there (the section only then has its full height), bring it into view, once per plan
  const shownFor = useRef(null)
  useEffect(() => {
    const k = planId ? `${caseKey}|${planId}` : null
    if (!k || !doc || shownFor.current === k) return
    shownFor.current = k
    requestAnimationFrame(() => draftRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
  }, [doc, planId, caseKey])

  const a = g.byId[o.a]
  const b = g.byId[o.b]
  const aShort = utilityShort(a?.utility || o.a_utility)
  const bShort = utilityShort(b?.utility || o.b_utility)
  const station = o.shared_station
  const title = station ? s.stationTitle(aShort, bShort, station.name) : s.title(aShort, bShort)
  const goStep = (n) => {
    if (n === 1) closeDraft()
    else g.setPairStep(n === 3 ? 'plans' : 'saves')
  }

  return (
    <aside ref={ref} className={`gl gl-sheet bt-sheet${step === 3 ? ' bt-sheet--wide' : ''}`} aria-labelledby="gl-sheet-h">
      <header className="gl-sheet__head bt-head">
        <div className="gl-sheet__top">
          {/* a phone shows the sheet full screen: its way back to the list (the close button is the desktop's) */}
          <button type="button" className="gl-back gl-sheet__back" onClick={closeDraft}>
            <span aria-hidden="true">‹</span> {s.back}
          </button>
          <div className="gl-sheet__titles">
            <h2 id="gl-sheet-h" ref={titleRef} tabIndex={-1}>
              {title}
            </h2>
            <p className="gl-sheet__sub">
              {listed ? s.pairOf(rank, g.ov.flagged) : s.outside}
              {lang === 'en' ? `: ${whereText(o).replace(/^./, (c) => c.toLowerCase())}, ${whenText(o)}` : ''}
            </p>
          </div>
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
          <button type="button" className="gl-close" onClick={closeDraft} aria-label={s.close} title={s.close}>
            ×
          </button>
        </div>
        {!covered && (
          <nav className="bt-steps" aria-label={lang === 'es' ? 'Pasos' : 'Steps'}>
            <ol>
              {s.steps.map((label, i) => {
                const n = i + 1
                const state = n < step ? 'done' : n === step ? 'now' : 'next'
                return (
                  <li key={label} className={`bt-steps__i is-${state}`}>
                    <button type="button" aria-current={state === 'now' ? 'step' : undefined} onClick={() => goStep(n)} title={n === 1 ? s.back : undefined}>
                      <span className="bt-steps__n" aria-hidden="true">
                        {state === 'done' ? (
                          <svg viewBox="0 0 16 16">
                            <path d="M3.5 8.5l3 3 6-7" />
                          </svg>
                        ) : (
                          n
                        )}
                      </span>
                      <span className="bt-steps__t">{label}</span>
                    </button>
                  </li>
                )
              })}
            </ol>
          </nav>
        )}
      </header>

      <div className="gl-sheet__main">
        <div className="gl-sheet__body bt-body" ref={bodyRef} inert={covered || undefined}>
          <h3 className="gl-sr" ref={stepRef} tabIndex={-1}>
            {s.steps[step - 1]}
          </h3>
          {step === 2 && (
            <PairSaves
              o={o}
              base={base}
              lang={lang}
              listed={listed}
              onNext={() => g.setPairStep('plans')}
              onOpenProject={openProject}
              onTrace={openTrace}
              onRetry={() => setTries((n) => n + 1)}
            />
          )}
          {step === 3 && (
            <>
              {plans.status === 'loading' && <PlansLoading names={[aShort, bShort]} lang={lang} ms={elapsed} />}
              {plans.status === 'error' && (
                <div className="bt-err">
                  <ErrorBanner
                    error={new Error(`${lang === 'es' ? 'No se pudieron preparar los planes' : "The plans couldn't be prepared"}: ${plans.error.message}`)}
                    onRetry={() => setPlanTries((n) => n + 1)}
                  />
                  <button type="button" className="gl-link" onClick={() => g.setPairStep('saves')}>
                    {s.back2}
                  </button>
                </div>
              )}
              {plans.status === 'ready' && (
                <PlanCards data={plans.data} lang={lang} chosen={planId} drafting={!!planId && !doc} onChoose={choose} />
              )}
              {plans.status === 'ready' && (
                <section className="bt-draft" ref={draftRef} aria-labelledby="bt-draft-h">
                  <div className="bt-draft__head">
                    <h3 id="bt-draft-h">{s.draftH}</h3>
                    {doc && (
                      <div className="bt-draft__tools">
                        <DraftStatus doc={doc} pending={aiPending} aiError={planAi.status === 'error' ? planAi.error : null} t={t} onRetry={() => setTries((n) => n + 1)} />
                        <button type="button" className="gl-tool" onClick={print} title={s.printTitle}>
                          <svg viewBox="0 0 16 16" aria-hidden="true">
                            <path d="M4.5 6V2.5h7V6M4.5 11.5h-2v-5h11v5h-2M4.5 9.5h7v4h-7z" />
                          </svg>
                          {s.print}
                        </button>
                        <button type="button" className="gl-tool" onClick={download} title={s.downloadTitle}>
                          <svg viewBox="0 0 16 16" aria-hidden="true">
                            <path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10" />
                          </svg>
                          {s.download}
                        </button>
                        {o.same_window && g.engineParams && (
                          <a className="gl-tool" href={downloadUrl('calendar.ics', g.engineParams, { pair: o.id })} download title={s.icsTitle}>
                            <svg viewBox="0 0 16 16" aria-hidden="true">
                              <path d="M2.5 4.5h11v9h-11zM2.5 7.5h11M5.5 2.5v3M10.5 2.5v3" />
                            </svg>
                            {s.ics}
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                  {!planId && <p className="bt-draft__pick">{s.pick}</p>}
                  {planId && !doc && !draftFailed && <Loading label={s.drafting} />}
                  {draftFailed && <ErrorBanner error={new Error("The draft couldn't be written. Check the connection and try again.")} onRetry={() => setTries((n) => n + 1)} />}
                  {doc && (
                    <>
                      {(doc.plan?.title || chosenPlan?.title) && <p className="bt-draft__from">{s.draftFrom(doc.plan?.title || chosenPlan.title)}</p>}
                      <div className="gl-desk">
                        <Paper doc={doc} t={t} />
                      </div>
                    </>
                  )}
                </section>
              )}
            </>
          )}
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
