// FEATURE: the siting planner on the map (owned by the planner track).
// Contract: default export PlannerLayer({view?}) — SVG inside the map camera (a GridMap child; it
// reads the zoom from useMapView(), or from `view` = {k, project} when portaled into the camera).
//
// While a plan runs, the sites of the step on screen: the ranked candidates as faint rings, the
// sites the planner tried as rings with their size (red when a line went over). When the plan is
// in, its campuses as numbered pins. Only on the plan's own state, and gone once the plan is loaded
// into the workspace (the real campus markers take over).
import { useMapView } from '../../GridMap'
import { useOverload } from '../../store'
import './planner.css'
import { revealed, usePlanner } from './plannerStore'

export default function PlannerLayer({ view }) {
  const ctx = useMapView()
  const { k, project } = view || ctx
  const { grid } = useOverload()
  const p = usePlanner()
  const region = p.request?.region
  if (p.status === 'idle' || p.loaded || !grid || (grid.meta?.region || 'FL') !== region) return null

  const done = revealed(p) && p.result?.plan
  const shown = p.steps.slice(0, p.shown)
  const last = [...shown].reverse().find((s) => s.sites?.length)
  const fs = 11 / k
  const at = (s) => project(s.lon, s.lat)

  if (done) {
    return (
      <g className="planner-layer" aria-hidden="true">
        {p.result.plan.sites.map((s, i) => {
          const [x, y] = at(s)
          return (
            <g key={s.sub} className="planner-pin" transform={`translate(${x} ${y})`}>
              <circle className="planner-pin__halo" r={18 / k} />
              <circle className="planner-pin__ring" r={11 / k} strokeWidth={1.5 / k} />
              <text className="planner-pin__n" fontSize={10 / k} dy={3.5 / k}>
                {i + 1}
              </text>
              <text className="planner-label" x={14 / k} y={19 / k} fontSize={fs} strokeWidth={3 / k}>
                {s.town} · {s.mw.toLocaleString('en-US')} MW
              </text>
            </g>
          )
        })}
      </g>
    )
  }
  if (!last) return null
  const tried = last.sites.some((s) => s.mw != null)
  return (
    <g className="planner-layer" aria-hidden="true">
      {last.sites.map((s) => {
        const [x, y] = at(s)
        if (!tried) {
          return (
            <g key={s.sub} className="planner-cand" transform={`translate(${x} ${y})`}>
              <circle className="planner-cand__ring" r={6 / k} strokeWidth={1 / k} />
              <text className="planner-label planner-label--faint" x={9 / k} y={3.5 / k} fontSize={fs * 0.9} strokeWidth={3 / k}>
                {s.town}
              </text>
            </g>
          )
        }
        const cls = last.ok === false ? ' planner-try--bad' : last.ok === true ? ' planner-try--ok' : ''
        return (
          <g key={s.sub} className={`planner-try${cls}`} transform={`translate(${x} ${y})`}>
            <circle className="planner-try__ring" r={12 / k} strokeWidth={1.5 / k} />
            <circle className="planner-try__dot" r={3 / k} />
            <text className="planner-label" x={14 / k} y={19 / k} fontSize={fs} strokeWidth={3 / k}>
              {s.town} · {s.mw.toLocaleString('en-US')} MW
            </text>
          </g>
        )
      })}
    </g>
  )
}
