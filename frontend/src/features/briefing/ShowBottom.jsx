import { useEffect, useMemo } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { reportPeople } from '../cost/figures'
import { areaShape, flexPin, keepsText, leadPoints, oftenText, pinsOf, rangeText } from './beatMaps'
import { Kicker } from './ShowBits'
import { MoreOptions } from './ShowSolutions'
import { mainOptions, moreOptions } from './showDeck'
import { P, S } from './showText'

// THE BOTTOM LINE: the options side by side against the blackout's cost, on one scale (the blackout's high end is the
// full width, each option's price a green bar drawn to the same scale, what each keeps beside it). The map shows the
// lead fix in green where it goes, its prices pinned, over the outline of the areas it keeps lit (hatched faintly: the
// blackout that would have been). A catastrophe with no fix keeps the Slide's own recovery picture.
export default function ShowBottom({ fixSlide, report, lang, animate, options, stage }) {
  const t = S[lang]
  const T = P[lang]
  const O = useOverload()
  const { grid, branchById, subPos } = O
  const main = useMemo(() => mainOptions(options), [options])
  const more = useMemo(() => moreOptions(options), [options])
  const present = fixSlide?.present || {}
  const blackout = present.blackout || null
  const lead = main[0]
  const { hit } = reportPeople(report)
  const bHi = Number(blackout?.high) || Number(report?.cost?.blackout_high_usd) || 0
  const bLo = Number(blackout?.low) || Number(report?.cost?.ranges?.blackout_usd?.[0]) || 0
  const often = oftenText(present.often, lang)

  useEffect(() => {
    if (!stage || !lead) return
    const areas = (report?.areas || []).filter((a) => Number(a.people) > 0).slice(0, 5)
    const hulls = areas.map((a) => ({ key: a.area, pts: areaShape({ grid }, a).pts, tone: 'dim' })).filter((h) => h.pts.length)
    const ids = lead.cost?.items?.length ? lead.cost.items.map((it) => it.id) : lead.lines.map((l) => l.id)
    const lines = ids.map((id, j) => ({ id, tone: 'fix', delay: animate ? 500 + j * 350 : 0 }))
    const tags = pinsOf({ branchById, subPos }, lead, lang, { delay: animate ? 700 : 0, step: animate ? 350 : 0 })
    const marks = []
    if (lead.family === 'flexible' && report?.case?.sub_lon != null) {
      const here = [report.case.sub_lon, report.case.sub_lat]
      marks.push({ at: here, tone: 'fix', r: 16 })
      if (lead.flex) tags.push({ at: here, ...flexPin(lead.flex, lang), tone: 'fix', delay: animate ? 700 : 0 }) // its step-down, where it runs
    }
    // power of its own leads (nothing keeps the full campus on the grid alone): the plant at the campus, and what the grid supplies
    if (lead.family === 'onsite' && lead.gen?.onsite_mw && report?.case?.sub_lon != null) {
      const here = [report.case.sub_lon, report.case.sub_lat]
      marks.push({ at: here, tone: 'fix', r: 16 })
      const W = P[lang]
      tags.push({ at: here, text: W.onsitePin(fmt(lead.gen.onsite_mw)), sub: lead.gen.net_mw != null ? W.onsitePinSub(fmt(lead.gen.net_mw)) : null, tone: 'fix', side: 'nw', delay: animate ? 700 : 0 })
    }
    stage.layer({ key: 'bottom', still: !animate, hulls, lines, tags, marks })
    const pts = [...leadPoints({ branchById, subPos }, lead), ...areas.map((a) => a.center).filter(Boolean)]
    if (report?.case?.sub_lon != null) pts.push([report.case.sub_lon, report.case.sub_lat])
    if (pts.length) stage.camera({ points: pts })
  }, [stage, lead, report, grid, branchById, subPos, lang, animate])
  useEffect(() => () => stage?.layer(null), [stage])

  const rows = [
    { key: 'lost', label: T.ifNothing, value: rangeText(bLo, bHi), meta: hit ? `${fmt(hit)} ${t.hit} (${lang === 'es' ? 'estimación' : 'estimate'})` : '', w: 100, tone: 'lost' },
    ...main.map((o, i) => {
      const hi = Number(o.cost?.high) || 0
      return {
        key: `o${i}`,
        label: o.name[lang],
        ai: o.by === 'gemini',
        value: hi ? rangeText(o.cost.low, hi) : o.family === 'flexible' ? T.noEquipment : t.costNone,
        meta: `${keepsText(o, T)} · ${Number(o.outcome?.people) ? t.peopleOutN(fmt(o.outcome.people)) : t.zeroOut}`,
        w: bHi && hi ? Math.max(0.6, Math.min(100, (hi / bHi) * 100)) : 0,
        tone: 'fix',
      }
    }),
  ]
  return (
    <section className={`sh-vs${animate ? ' sh-vs--anim' : ''}`} aria-label={T.compared}>
      <Kicker tone="green">{T.compared}</Kicker>
      <ol className="sh-vs__rows">
        {rows.map((r, i) => (
          <li key={r.key} className={`sh-vs__row sh-vs__row--${r.tone}`} style={{ '--w': `${r.w}%`, '--d': `${300 + i * 450}ms` }}>
            <p className="sh-vs__label">
              {r.label}
              {r.ai && <em className="sh-ai">{t.aiPlan}</em>}
            </p>
            <span className="sh-vs__bar" aria-hidden="true">
              <i />
            </span>
            <p className="sh-vs__value">
              <b>{r.value}</b>
              {r.meta && <span>{r.meta}</span>}
            </p>
          </li>
        ))}
      </ol>
      {often && <p className="sh-weigh__often">{often}</p>}
      <MoreOptions more={more} lang={lang} compact />
    </section>
  )
}
