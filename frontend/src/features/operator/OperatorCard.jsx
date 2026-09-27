// FEATURE: "Let an AI operator try" (CLAUDE.md -> Decisions: MORE AGENTS, GEMINI MAX). In the results column, once the
// cascade has played: one button, then the same case is fought three ways on the SYNTHETIC grid model (backend/
// grid_operator.py): no operator, the engine's own operator, and a Gemini operator that reads the alarm at each step,
// calls engine tools (which lines are over, which plants can move their flow, where cutting load relieves them, "play
// this forward") and applies up to 3 moves a step. The engine validates and applies every move and re-solves; Gemini
// never supplies a number. Load it cuts counts as people hit, so it can't win by hiding the loss. The honest result is
// shown as it is: the AI can do better than nothing, match it, or be beaten by the engine's operator.
//
// Props: lang 'en' | 'es'. Mounted once in shell/ImpactPanel.jsx (after DarkFirst). Nothing here plays audio.
import { useEffect, useMemo, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import AgentTrace, { AgentTraceToggle } from '../ai/AgentTrace'
import AiBadge from '../ai/AiBadge'
import { rulesBody } from '../darkfirst/rulesStore'
import { plantsOut } from '../fix/flipCase'
import './operator.css'
import { useOperator } from './operatorApi'

const T = {
  en: {
    h: 'Could an operator have stopped it?',
    lead: 'A test on the synthetic model. An AI operator gets the alarm at each step and may move power between plants, cut load or open a line; the engine checks every move, re-solves, and trips what is still over its rating.',
    go: 'Let an AI operator try',
    again: 'Try again',
    working: 'The engine is checking every move…',
    slow: 'This can take a minute on this server.',
    saved: 'A saved run of this exact case, recorded from a live Gemini run. Any other site or size runs live.',
    engineFirst: (n, z) => `Engine operator: ${fmt(n)} people hit (no operator: ${fmt(z)})`,
    step: (t, n) => `Gemini is fighting step ${t} of ${n}…`,
    calls: (n) => `${n} ${n === 1 ? 'call' : 'calls'} so far`,
    withOp: 'With a Gemini operator',
    withEngine: 'Engine operator (AI unavailable)',
    people: 'people hit',
    instead: (z) => `instead of ${fmt(z)}`,
    same: 'the same as with no operator',
    sameEnd: 'the final blackout is about the same size',
    partial: (n) => `Gemini answered steps 1–${n}; the rest of the cascade ran without its moves.`,
    moves: (k) => `${k} ${k === 1 ? 'move' : 'moves'}`,
    noMoves: 'no moves helped',
    tableHead: ['', 'People hit', 'Still dark', 'Tripped'],
    rows: { none: 'No operator', engine: 'Engine operator', gemini: 'Gemini operator' },
    best: 'fewest hit',
    movesH: "The operator's moves",
    stepN: (n) => `Step ${n}`,
    then: (pct) => `worst line then ${pct} % of its rating`,
    then0: 'no line left over its rating',
    trace: (n) => `Watch the AI work (${n} tool calls)`,
    engineTrace: 'How the engine operator decided',
    print: 'Moves in the synthetic model, not any utility’s or grid operator’s procedure. Load the operator cuts is counted as people hit. Estimates.',
    err: 'The operator run failed.',
  },
  es: {
    h: '¿Pudo un operador haberlo detenido?',
    lead: 'Una prueba sobre el modelo sintético. Un operador de IA recibe la alarma en cada paso y puede mover potencia entre plantas, cortar carga o abrir una línea; el motor comprueba cada movimiento, recalcula y dispara lo que sigue sobre su límite.',
    go: 'Dejar que un operador de IA lo intente',
    again: 'Reintentar',
    working: 'El motor está comprobando cada movimiento…',
    slow: 'Esto puede tardar un minuto en este servidor.',
    saved: 'Una ejecución guardada de este caso exacto, grabada de una ejecución en vivo de Gemini. Cualquier otro sitio o tamaño se ejecuta en vivo.',
    engineFirst: (n, z) => `Operador del motor: ${fmt(n)} personas afectadas (sin operador: ${fmt(z)})`,
    step: (t, n) => `Gemini lucha en el paso ${t} de ${n}…`,
    calls: (n) => `${n} ${n === 1 ? 'llamada' : 'llamadas'} hasta ahora`,
    withOp: 'Con un operador Gemini',
    withEngine: 'Operador del motor (IA no disponible)',
    people: 'personas afectadas',
    instead: (z) => `en vez de ${fmt(z)}`,
    same: 'igual que sin operador',
    sameEnd: 'el apagón final es de tamaño parecido',
    partial: (n) => `Gemini respondió los pasos 1–${n}; el resto de la cascada siguió sin sus movimientos.`,
    moves: (k) => `${k} ${k === 1 ? 'movimiento' : 'movimientos'}`,
    noMoves: 'ningún movimiento ayudó',
    tableHead: ['', 'Afectadas', 'Sin luz al final', 'Disparadas'],
    rows: { none: 'Sin operador', engine: 'Operador del motor', gemini: 'Operador Gemini' },
    best: 'menos afectadas',
    movesH: 'Movimientos del operador',
    stepN: (n) => `Paso ${n}`,
    then: (pct) => `la peor línea quedó al ${pct} % de su límite`,
    then0: 'ninguna línea sobre su límite',
    trace: (n) => `Mira trabajar a la IA (${n} llamadas a herramientas)`,
    engineTrace: 'Cómo decidió el operador del motor',
    print: 'Movimientos en el modelo sintético, no el procedimiento de ninguna empresa ni operador de red. La carga que corta el operador cuenta como personas afectadas. Estimaciones.',
    err: 'La ejecución del operador falló.',
  },
}

const moveText = (a, lang) => {
  const es = lang === 'es'
  if (a.type === 'redispatch') return es ? `Movió ${fmt(a.mw)} MW de ${a.lower.name} a ${a.raise.name}` : `Moved ${fmt(a.mw)} MW from ${a.lower.name} to ${a.raise.name}`
  if (a.type === 'shed') return es ? `Cortó ${fmt(a.mw)} MW en ${a.name}` : `Cut ${fmt(a.mw)} MW ${a.substation != null ? 'at' : 'across'} ${a.name}`
  return es ? `Abrió ${a.name}` : `Opened ${a.name}`
}

// the moves of one run, in order, each with what the engine found after it
function movesOf(run) {
  const out = []
  for (const s of run?.steps || []) {
    const op = s.operator
    if (!op?.applied?.length) continue
    out.push({ n: op.turn, moves: op.applied, after: op.over_after?.[0]?.pct ?? null })
  }
  return out
}

export default function OperatorCard({ lang = 'en' }) {
  const t = T[lang] || T.en
  const O = useOverload()
  const { cascade, caseBody } = O
  const body = useMemo(() => (!cascade || cascade.firm || plantsOut(cascade) ? null : rulesBody(caseBody, cascade)), [caseBody, cascade])
  const op = useOperator(body)
  if (!body) return null
  const { status, progress, runs, trace, result, startedAt } = op
  return (
    <section className="op" aria-labelledby="op-h" aria-busy={status === 'running'}>
      <h2 className="panel-h" id="op-h">
        {t.h}
      </h2>
      {status === 'idle' && (
        <>
          <p className="op__lead">{t.lead}</p>
          <button type="button" className="op__go" onClick={op.start}>
            {t.go}
          </button>
        </>
      )}
      {status === 'running' && <Working t={t} progress={progress} runs={runs} trace={trace} startedAt={startedAt} />}
      {status === 'error' && (
        <p className="op__err" role="alert">
          {op.error?.message || t.err}{' '}
          <button type="button" className="op__again" onClick={op.start}>
            {t.again}
          </button>
        </p>
      )}
      {status === 'done' && result && <Result t={t} lang={lang} r={result} />}
    </section>
  )
}

// milliseconds since the run started, ticking once a second while the card is on screen
function useElapsed(startedAt) {
  const [now, setNow] = useState(startedAt || 0)
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  return startedAt && now > startedAt ? now - startedAt : 0
}

const SLOW_MS = 60000 // past this the card says once that a live run can take a minute

function Working({ t, progress, runs, trace, startedAt }) {
  const ms = useElapsed(startedAt)
  const gem = progress.phase === 'gemini'
  // the steps the server has finished, and under them a floor that rises with the clock (never reaching the end), so the bar
  // is always moving while a slow server works between two answers; the call count, the step and the trace say what is happening
  const real = gem ? 0.1 + 0.86 * ((progress.turn || 0) / (progress.turns || 6)) : 0.06 + 0.04 * Math.min(1, (progress.engine_turn || 0) / 5)
  const share = Math.min(0.96, Math.max(real, 0.05 + 0.9 * (1 - Math.exp(-ms / 45000))))
  return (
    <div className="op__work" role="status">
      <div className="op__bar" aria-hidden="true">
        <span style={{ transform: `scaleX(${share})` }} />
      </div>
      <p className="op__wait">
        {runs.engine && runs.none ? t.engineFirst(runs.engine.people_hit, runs.none.people_hit) : t.working}
        {gem && (
          <>
            <br />
            {t.step(Math.max(1, progress.turn || 1), progress.turns || 6)} {progress.calls > 0 && <span className="op__dim">{t.calls(progress.calls)}</span>}
          </>
        )}
      </p>
      {ms >= SLOW_MS && <p className="op__wait">{t.slow}</p>}
      {trace.length > 0 && <AgentTrace trace={trace} lang="en" live follow brief heading={false} animate={false} className="op__trace" />}
    </div>
  )
}

function Result({ t, lang, r }) {
  const shownKey = r.shown === 'gemini' ? 'gemini' : 'engine'
  const shown = r.runs[shownKey]
  const none = r.runs.none
  const order = ['none', 'engine', ...(r.runs.gemini ? ['gemini'] : [])]
  const most = Math.min(...order.map((k) => r.runs[k].toll.people_hit))
  const z = none.toll.people_hit
  const n = shown.toll.people_hit
  const delta = z > 0 ? Math.round(((n - z) / z) * 100) : 0
  const real = r.verdict?.real !== false  // false: fewer counted as hit, but the final blackout is as big as with no operator
  const endSame = none.toll.people_out > 0 && shown.toll.people_out >= 0.95 * none.toll.people_out  // the shown run's final blackout is no smaller
  const tone = delta < -1 && !endSame ? 'good' : delta > 1 ? 'bad' : 'flat'
  const stoppedAt = r.by === 'gemini' ? r.runs.gemini?.stopped_at : null
  const k = (shown.actions?.redispatch || 0) + (shown.actions?.shed || 0) + (shown.actions?.open_line || 0)
  const list = movesOf(shown)
  return (
    <div className="op__res">
      <div className="op__head">
        <p className="op__k">
          {r.by === 'gemini' ? t.withOp : t.withEngine}{' '}
          {r.by === 'gemini' ? <AiBadge by="gemini" verified compact lang={lang} /> : <AiBadge by="fallback" why={r.why} compact lang={lang} />}
        </p>
        <p className={`op__n op__n--${tone}`}>
          <span className="op__fig">{fmt(n)}</span> <span className="op__unit">{t.people}</span>
        </p>
        <p className="op__vs">
          {tone === 'flat' && Math.abs(delta) <= 1 ? t.same : t.instead(z)}
          {tone === 'flat' && delta < 0 && endSame && ` · ${t.sameEnd}`}
          {tone !== 'flat' && <span className={`op__delta op__delta--${tone}`}>{` ${delta > 0 ? '+' : '−'}${Math.abs(delta)} %`}</span>}
          {' · '}
          {k > 0 ? t.moves(k) : t.noMoves}
        </p>
      </div>
      <table className="op__tbl">
        <thead>
          <tr>
            {t.tableHead.map((h, i) => (
              <th key={i} scope="col" className={i ? 'op__num' : undefined}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {order.map((key) => {
            const tl = r.runs[key].toll
            const best = real && tl.people_hit === most && most < z
            return (
              <tr key={key} className={key === shownKey ? 'is-shown' : undefined}>
                <th scope="row">
                  {t.rows[key]}
                  {best && <span className="op__best">{t.best}</span>}
                </th>
                <td className="op__num">{fmt(tl.people_hit)}</td>
                <td className="op__num">{fmt(tl.people_out)}</td>
                <td className="op__num">{tl.tripped}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="op__verdict">{r.verdict.text}</p>
      {r.verdict.note && <p className="op__note">{r.verdict.note}</p>}
      {r.baked && <p className="op__note">{t.saved}</p>}
      {stoppedAt > 1 && <p className="op__note">{t.partial(stoppedAt - 1)}</p>}
      {list.length > 0 && (
        <div className="op__moves">
          <p className="op__k">{t.movesH}</p>
          <ol>
            {list.map((s) => (
              <li key={s.n}>
                <span className="op__step">{t.stepN(s.n)}</span>
                {s.moves.map((a, i) => (
                  <span key={i} className="op__mv">
                    {moveText(a, lang)}
                  </span>
                ))}
                <span className="op__dim">{s.after == null ? t.then0 : t.then(Math.round(s.after))}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
      <AgentTraceToggle
        trace={r.trace}
        lang="en"
        label={r.by === 'gemini' ? t.trace(r.tool_calls) : t.engineTrace}
        heading={false}
        brief
        className="op__trace"
        stepMs={260}
      />
      <p className="op__print">{t.print}</p>
    </div>
  )
}
