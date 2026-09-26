// FEATURE: AI-boom mode — labels on the map (owned by the boom track).
// Contract: default export BoomLayer() — SVG inside the map camera (map units; sizes divided by
// the zoom so the labels stay the same size on screen). GridMap draws the campuses' white squares;
// this adds their size ("1 GW") and, in AI-boom mode, a ring on the line that breaks first.
// While the siting agent works (BoomAgent), it also shows what the agent is doing: the substations it
// is ranking (faint rings with their room), a ring that closes on each campus as it lands (red while
// the engine finds a line over, green once it holds), and the lines its plan upgrades (dashed while
// proposed, solid once the plan is in). It also hosts the agent's sync with the map (agentSync.js).
import { useMapView } from '../../GridMap'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { revealed, usePlanner } from '../planner/plannerStore'
import { lastPlaced, useAgentSync, useShownVersion } from './agentSync'
import { sizeLabel } from './boomData'
import './boom.css'

const CAND_LABELS = 5 // label the first few candidates; the rest are rings

export default function BoomLayer() {
  useAgentSync()
  const { extraSites, mode, result, cascade, step, subPos, region, grid } = useOverload()
  const { k, project } = useMapView()
  const p = usePlanner()
  const version = useShownVersion()
  const agent = agentMarks(p, region, grid, version)
  if (!extraSites.length && !agent) return null
  const u = 1 / k // one screen-ish unit at this zoom

  // the most overloaded line of the combined case: the cascade trips it first
  let first = null
  const worst = mode === 'boom' && !(cascade && step > 0) ? result?.overloaded?.[0] : null
  const campusSubs = new Set(extraSites.map((c) => c.sub))
  if (worst && (result.sites || []).some((s) => campusSubs.has(s.sub))) {
    const a = subPos(worst.from)
    const b = subPos(worst.to)
    if (a && b) {
      const [x1, y1] = project(a[0], a[1])
      const [x2, y2] = project(b[0], b[1])
      first = [(x1 + x2) / 2, (y1 + y2) / 2]
    }
  }

  return (
    <g className="boom-layer" aria-hidden="true">
      {agent && <AgentMarks agent={agent} u={u} project={project} subPos={subPos} />}
      {extraSites.map((c) => {
        const [x, y] = project(c.lon, c.lat)
        return (
          // under the square: city names sit above-right of a point, town impacts to its right
          <text key={c.id} className="boom-tag" x={x} y={y + 19 * u} fontSize={11 * u} strokeWidth={3 * u}>
            {sizeLabel(c.mw)}
          </text>
        )
      })}
      {first && (
        <g className="boom-first" transform={`translate(${first[0]} ${first[1]})`}>
          <circle className="boom-first__ring" r={10 * u} />
          <circle className="boom-first__ring boom-first__ring--pulse" r={10 * u} />
          <text className="boom-first__label" x={-12 * u} y={-10 * u} fontSize={11 * u} strokeWidth={3 * u}>
            Breaks first
          </text>
        </g>
      )}
    </g>
  )
}

// What the agent's trace says the map should mark right now, or null (not the boom panel's run, or
// another state on screen).
function agentMarks(p, region, grid, version) {
  if (p.origin !== 'boom' || p.status === 'idle' || !p.request) return null
  const gridRegion = grid ? grid.meta?.region || 'FL' : null
  if (p.request.region !== region || gridRegion !== region) return null
  const shown = p.steps.slice(0, p.shown).filter((s) => s.tool !== 'note')
  const done = revealed(p) && p.result?.plan ? p.result : null
  const newest = shown[shown.length - 1]
  const cands = !done && newest?.tool === 'headroom_top' ? newest.sites || [] : []
  let sites = []
  let tone = 'try'
  let ups = []
  let upsApplied = false
  if (done) {
    const alt = version === 'alt' && done.without_upgrades
    sites = alt ? done.without_upgrades.sites : done.plan.sites
    tone = (alt ? done.without_upgrades.verification?.ok : done.verification?.ok) ? 'ok' : 'bad'
    ups = alt ? [] : done.plan.upgrade_lines || []
    upsApplied = true
  } else {
    const placed = lastPlaced(shown)
    sites = placed?.sites || []
    tone = newest?.ok === true ? 'ok' : newest?.ok === false ? 'bad' : 'try'
    ups = newest?.upgrade_lines || []
    upsApplied = newest?.tool === 'finish'
  }
  if (!cands.length && !sites.length && !ups.length) return null
  return { runId: p.runId, cands, sites, tone, ups, upsApplied }
}

function AgentMarks({ agent, u, project, subPos }) {
  const { runId, cands, sites, tone, ups, upsApplied } = agent
  return (
    <g className="boom-agent-layer">
      {ups.map((l) => {
        const a = subPos(l.from)
        const b = subPos(l.to)
        if (!a || !b) return null
        const [x1, y1] = project(a[0], a[1])
        const [x2, y2] = project(b[0], b[1])
        const cls = `boom-up${upsApplied ? '' : ' boom-up--proposed'}`
        return (
          <g key={`${runId}-up-${l.id}`} className={cls}>
            {l.from === l.to ? <circle className="boom-up__line" cx={x1} cy={y1} r={9 * u} /> : <line className="boom-up__line" x1={x1} y1={y1} x2={x2} y2={y2} />}
            <text className="boom-up__label" x={Math.max(x1, x2) + 12 * u} y={(y1 + y2) / 2 + 4 * u} fontSize={10.5 * u} strokeWidth={3 * u}>
              +{fmt(l.added_mva)} MVA
            </text>
          </g>
        )
      })}
      {cands.map((c, i) => {
        const [x, y] = project(c.lon, c.lat)
        return (
          <g key={`${runId}-cand-${c.sub}`} className="boom-cand" style={{ animationDelay: `${i * 70}ms` }} transform={`translate(${x} ${y})`}>
            <circle className="boom-cand__ring" r={7 * u} strokeWidth={1 * u} />
            {i < CAND_LABELS && (
              <text className="boom-cand__label" x={10 * u} y={3.5 * u} fontSize={10 * u} strokeWidth={3 * u}>
                {c.town} · {fmt(c.room_mw)} MW
              </text>
            )}
          </g>
        )
      })}
      {sites.map((s) => {
        const [x, y] = project(s.lon, s.lat)
        return (
          // keyed by the run and the substation: the ring closes once when a campus lands there
          <g key={`${runId}-site-${s.sub}`} className={`boom-drop boom-drop--${tone}`} transform={`translate(${x} ${y})`}>
            <circle className="boom-drop__ring" r={12 * u} />
          </g>
        )
      })}
    </g>
  )
}
