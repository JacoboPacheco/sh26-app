// Build agreement: words and the plain-text (.md) version of a draft. The draft itself comes from the
// backend (backend/agreement.py); this only labels it and writes it out as text for the download.
import { MONTHS, MONTHS_ES, fmtDate, fmtRange } from './format'

export const LABELS = {
  en: {
    lang: 'en',
    module: 'Build agreement',
    eyebrow: 'Draft coordination proposal',
    parties: 'Parties',
    scope: 'Shared scope',
    window: 'Joint build window',
    windowPast: 'Build windows, as filed',
    roles: 'Who does what',
    split: 'Cost split',
    savings: 'Estimated savings',
    why: 'Why these two projects',
    steps: 'Next steps',
    conditions: 'Conditions',
    facts: 'Facts used and where each comes from',
    both: 'Both utilities',
    proposedRule: 'A rule this draft proposes, not a figure from either filing.',
    derived: 'start derived',
    joint: 'Joint window',
    jointOpen: 'Open now, to',
    jointPast: 'Shared as filed, passed',
    today: 'Today,',
    noJoint: 'No shared months',
    filed: 'Filed in',
    inService: 'in service',
    roughEstimate: 'rough estimate, if the two projects share what their distance and timing allow',
    assumptions: 'Assumptions',
    checked: 'Every number checked against the facts',
    drafting: 'Gemini is drafting; this is the plain version until its words pass the checks',
    removed: (n) => `The checker removed ${n} sentence${n === 1 ? '' : 's'} Gemini wrote`,
    print: 'Print or save as PDF',
    retry: 'Try Gemini again',
    download: 'Download as text',
    close: 'Close the draft',
    generated: 'Generated',
    by: { gemini: 'Worded by Gemini from the facts; every number checked against them.', template: 'Written by the plain template from the facts.' },
    negotiated: {
      gemini: "Terms proposed by two AI agents, each reading one utility's public filing, verified against the filings",
      plain: 'Terms from the plain rule-based comparison of the two filings, verified against the filings',
    },
    negRound: (r) => `agreed in round ${r}`,
    negTag: 'Chosen plan',
    dropNeg: "Use the draft's own terms",
    needs: 'Needs',
  },
  es: {
    lang: 'es',
    module: 'Acuerdo de obra',
    eyebrow: 'Borrador de propuesta de coordinación',
    parties: 'Partes',
    scope: 'Alcance compartido',
    window: 'Ventana de obra conjunta',
    windowPast: 'Ventanas de obra, según lo publicado',
    roles: 'Quién hace qué',
    split: 'Reparto de costos',
    savings: 'Ahorro estimado',
    why: 'Por qué estos dos proyectos',
    steps: 'Próximos pasos',
    conditions: 'Condiciones',
    facts: 'Datos usados y de dónde viene cada uno',
    both: 'Ambas empresas',
    proposedRule: 'Una regla que propone este borrador, no una cifra de ninguno de los documentos.',
    derived: 'inicio derivado',
    joint: 'Ventana conjunta',
    jointOpen: 'Abierta ahora, hasta',
    jointPast: 'Compartida según lo publicado, ya pasó',
    today: 'Hoy,',
    noJoint: 'Sin meses en común',
    filed: 'Publicado en',
    inService: 'en servicio',
    roughEstimate: 'estimación aproximada, si los dos proyectos comparten lo que su distancia y calendario permiten',
    assumptions: 'Supuestos',
    checked: 'Cada número comprobado contra los datos',
    drafting: 'Gemini está redactando; esta es la versión simple hasta que su texto pase las comprobaciones',
    removed: (n) => `La comprobación quitó ${n} frase${n === 1 ? '' : 's'} de Gemini`,
    print: 'Imprimir o guardar como PDF',
    retry: 'Reintentar con Gemini',
    download: 'Descargar como texto',
    close: 'Cerrar el borrador',
    generated: 'Generado',
    by: { gemini: 'Redactado por Gemini a partir de los datos; cada número comprobado.', template: 'Escrito por la plantilla simple a partir de los datos.' },
    negotiated: {
      gemini: 'Términos propuestos por dos agentes de IA, cada uno con el documento público de una empresa, verificados con los documentos',
      plain: 'Términos de la comparación simple basada en reglas de los dos documentos, verificados con los documentos',
    },
    negRound: (r) => `acordados en la ronda ${r}`,
    negTag: 'Plan elegido',
    dropNeg: 'Usar los términos propios del borrador',
    needs: 'Requiere',
  },
}

export function fmtMonth(iso, lang = 'en') {
  if (!iso) return '–'
  const [y, m] = iso.split('-').map(Number)
  return m ? `${(lang === 'es' ? MONTHS_ES : MONTHS)[m - 1]} ${y}` : String(y)
}

// the sources an item's facts point to, one per label (the draft cites fact keys; the page shows sources)
export function sourcesFor(keys, byKey) {
  const out = []
  const seen = new Set()
  for (const k of keys || []) {
    const f = byKey[k]
    if (!f?.source) continue
    const label = f.source.label || f.source.title
    if (seen.has(label)) {
      out.find((s) => s.label === label).facts.push(f)
      continue
    }
    seen.add(label)
    out.push({ label, url: f.source.url, kind: f.source.kind, key: k, facts: [f] })
  }
  return out
}

function refText(item, byKey) {
  const s = sourcesFor(item.facts, byKey).map((x) => x.label)
  return s.length ? ` _[${s.join('; ')}]_` : ''
}

export function agreementMarkdown(doc, lang = 'en') {
  const t = LABELS[lang] || LABELS.en
  const d = doc.draft
  const byKey = Object.fromEntries((doc.facts || []).map((f) => [f.key, f]))
  const projects = doc.overlap?.projects || []
  const L = []
  L.push(`# ${d.title}`, '', `> ${doc.disclaimer}`, '')
  // a draft from a chosen collaboration plan says so in its own words; a negotiated one names the negotiation
  if (doc.plan?.applied && doc.plan.text) L.push(`> ${doc.plan.text.replace(/\.$/, '')}.`, '')
  else if (d.negotiated) L.push(`> ${t.negotiated[d.negotiated.by] || d.negotiated.text}${d.negotiated.round ? ` (${t.negRound(d.negotiated.round)})` : ''}.`, '')
  L.push(`${t.generated} ${new Date().toISOString().slice(0, 10)} · ${doc.overlap?.tier_label || ''} · ${doc.overlap_id}`, '')
  L.push(`## ${t.parties}`, '')
  for (const p of projects) {
    const bits = [p.kv?.length ? `${p.kv.join(' / ')} kV` : null, p.kind_label, p.in_service ? `${t.inService} ${fmtDate(p.in_service, t.lang)}` : null, p.status]
    L.push(`- **${p.utility_name}** (${p.utility}): ${p.display_name} (${p.id}); ${bits.filter(Boolean).join(', ')}. ${t.filed}: ${p.source?.label}${p.source?.url ? ` <${p.source.url}>` : ''}`)
  }
  L.push('', d.summary.text + refText(d.summary, byKey), '')
  const section = (title, items, ordered = false) => {
    L.push(`## ${title}`, '')
    items.forEach((it, i) => L.push(`${ordered ? `${i + 1}.` : '-'} ${it.text}${refText(it, byKey)}`))
    L.push('')
  }
  const scope = d.sections.find((s) => s.id === 'scope')
  const why = d.sections.find((s) => s.id === 'why')
  if (scope) section(`1. ${t.scope}`, scope.items)
  const jw = d.joint_window
  const past = jw.status === 'past' || (!jw.overlap && jw.past_sides?.length)
  L.push(`## 2. ${past ? t.windowPast : t.window}`, '', jw.text + refText(jw, byKey), '')
  if (jw.as_of) L.push(`${t.today.replace(/,$/, '')}: ${fmtDate(jw.as_of, t.lang)}. ${jw.status === 'past' ? `${t.jointPast}: ${fmtMonth(jw.start, t.lang)} – ${fmtMonth(jw.end, t.lang)}.` : ''}`.trim(), '')
  for (const [side, w] of [
    ['a', jw.a],
    ['b', jw.b],
  ]) {
    const p = d.parties.find((x) => x.side === side)
    if (w) L.push(`- ${p?.short}: ${fmtMonth(w.start, t.lang)} – ${fmtMonth(w.end, t.lang)} (${w.basis})`)
  }
  L.push('', `## 3. ${t.roles}`, '')
  for (const r of d.roles) {
    L.push(`### ${r.side === 'both' ? t.both : r.name}`, '')
    r.does.forEach((it) => L.push(`- ${it.text}${refText(it, byKey)}`))
    L.push('')
  }
  const cs = d.cost_split
  L.push(`## 4. ${t.split}`, '', cs.rule, '', cs.rationale + refText(cs, byKey), '', `_${t.proposedRule}_`, '')
  const sv = d.savings
  L.push(`## 5. ${t.savings}`, '', `**${fmtRange(sv.low, sv.high, sv.unit)}** (${sv.label.toLowerCase()})`, '')
  for (const it of sv.items || []) {
    const src = (sv.sources || []).find((s) => s.title === it.source)
    L.push(`- ${it.label}: ${fmtRange(it.low, it.high, it.unit)}. ${it.basis}. Source: ${it.source}${src?.url ? ` <${src.url}>` : ''}`)
  }
  if (sv.assumptions?.length) {
    L.push('', `${t.assumptions}:`, '')
    sv.assumptions.forEach((a) => L.push(`- ${a}`))
  }
  L.push('')
  if (why) section(`6. ${t.why}`, why.items)
  section(`7. ${t.steps}`, d.next_steps, true)
  section(`8. ${t.conditions}`, d.conditions)
  L.push(`## ${t.facts}`, '')
  for (const f of doc.facts || []) L.push(`- ${f.text} (${f.source?.label}${f.source?.url ? `, <${f.source.url}>` : ''})`)
  L.push('', '---', '', doc.by === 'gemini' ? t.by.gemini : t.by.template, doc.generated_from || '')
  return L.join('\n')
}
