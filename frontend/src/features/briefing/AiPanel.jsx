import { useMemo, useState } from 'react'
import AgentTrace from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { compactItems, groupTrace } from '../ai/trace'
import { AI_BEAT, agenticOf, mainOptions } from './showDeck'
import { P, S } from './showText'

// "How the AI found them", in the right-hand panel beside the options (user, Sat 19:12: "instead of just covering it
// with the how-AI-found-it, open a new panel on the right side of the screen while people can review the options").
// The AI proposer's own run (solutions.py → the fix slide's agentic.trace): what Gemini proposed, what the engine found
// when it re-ran the case, the findings sent back, the revision that held. Every row is a step the backend recorded.
//   fixSlide  the deck's fix slide (its agentic block; the deck's own as a fallback)
//   options   optionsOf(report, fixSlide): where the verified AI plans ended up (an option, or a variant of one)
//   mode      'auto' (the options' review beat drives the reveal: `shown` items), 'done' (that beat is over: all of
//             them, still), 'manual' (opened by the viewer: they reveal on their own)
export default function AiPanel({ fixSlide, deck, options, lang, mode = 'manual', shown = null }) {
  const t = S[lang]
  const T = P[lang]
  const [every, setEvery] = useState(false)
  const run = agenticOf(fixSlide, deck)
  const total = useMemo(() => (run ? groupTrace(run.trace).length : 0), [run])
  const brief = useMemo(() => (run ? compactItems(groupTrace(run.trace), AI_BEAT.items).length : 0), [run])
  const ag = fixSlide?.agentic || deck?.agentic || null
  if (!run) {
    return <p className="rs-aipanel__empty">{ag?.status === 'running' ? t.aiThinking : T.aiPanelOff}</p>
  }
  const main = mainOptions(options || [])
  // where Gemini's verified plans are in the options: its own option, or variants folded into one
  const where = []
  main.forEach((o, i) => {
    if (o.by === 'gemini') where.push(T.aiPanelWhereOwn(i + 1))
    const n = (o.variants || []).filter((v) => v.by === 'gemini').length
    if (n) where.push(T.aiPanelWhere(n, i + 1))
  })
  const full = every && mode !== 'auto'
  return (
    <div className="rs-aipanel">
      <p className="rs-aipanel__count">
        <AiBadge by="gemini" verified lang={lang} compact />
        <span>{run.asked > 0 ? t.aiWorkCount(run.asked, run.verified || 0, run.rounds || 1) : t.aiWork}</span>
      </p>
      <p className="rs-aipanel__lead">{T.aiPanelLead}</p>
      {where.length > 0 && <p className="rs-aipanel__where">{where.join(' ')}</p>}
      <AgentTrace
        key={full ? 'every' : 'brief'}
        trace={run.trace}
        lang={lang}
        shown={mode === 'auto' ? shown : null}
        animate={mode === 'manual' && !full}
        stepMs={mode === 'auto' ? AI_BEAT.step : 420}
        max={full ? undefined : AI_BEAT.items}
        brief={!full}
        heading={false}
        follow
        className="rs-aipanel__trace"
      />
      {mode !== 'auto' && total > brief && (
        <button type="button" className="rs-aipanel__every" aria-pressed={full} onClick={() => setEvery((v) => !v)}>
          {full ? T.aiPanelFewer : T.aiPanelEvery(total)}
        </button>
      )}
    </div>
  )
}
