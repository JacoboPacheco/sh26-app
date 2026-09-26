// Before and after, the study's own numbers: today (no upgrades) against the whole plan, each re-run by the
// engine, with where the budget stands. Three figures that say what strengthening does: fewer sites that set off
// a blackout, a much smaller worst blackout, far fewer lines pushed over their rating. Plain figures; amber for
// today's strain, green for the fixed grid (red is kept for what is lost, on the other page).
import { fmt } from '../../geo'
import { shortMoney } from './budget'
import { compact } from './unlockStore'

function Tile({ label, before, after, bNum, aNum, note }) {
  const w = bNum > 0 ? Math.max(2, (aNum / bNum) * 100) : 0
  return (
    <div className="st-ba__tile">
      <dt>{label}</dt>
      <dd>
        <span className="st-ba__fig">
          <span className="st-ba__before">{before}</span>
          <span className="st-ba__arrow" aria-hidden="true">
            →
          </span>
          <span className="st-sr">to</span>
          <span className="st-ba__after">{after}</span>
        </span>
        <span className="st-ba__bars" aria-hidden="true">
          <span className="st-ba__bar st-ba__bar--before" />
          <span className="st-ba__bar st-ba__bar--after" style={{ width: `${w}%` }} />
        </span>
        {note && <span className="st-ba__note">{note}</span>}
      </dd>
    </div>
  )
}

export default function BeforeAfter({ r, a, budget }) {
  const s = r.headline?.strain
  if (!s) return null
  const with_ = `With ${shortMoney(budget)}`
  return (
    <section className="st-ba" aria-labelledby="st-ba-h">
      <h2 className="st-h" id="st-ba-h">
        Today → with the whole plan{' '}
        <span className="st-h__sub">
          ({shortMoney(r.headline.cost_high)}, {fmt(r.headline.upgrades)} upgrades, each site re-run)
        </span>
      </h2>
      <dl className="st-ba__tiles">
        <Tile
          label="Sites that set off a blackout"
          before={fmt(s.blackout_sites_before)}
          after={fmt(s.blackout_sites_after)}
          bNum={s.blackout_sites_before}
          aNum={s.blackout_sites_after}
          note={`${with_}: ${fmt(a.prevented)} prevented`}
        />
        <Tile
          label="Worst blackout, people hit (estimate)"
          before={s.worst_people_before ? compact(s.worst_people_before) : 'none'}
          after={s.worst_people_after ? compact(s.worst_people_after) : 'none'}
          bNum={s.worst_people_before}
          aNum={s.worst_people_after}
          note={`${with_}: ${a.gone ? `the ${a.gone === 1 ? 'biggest' : `${fmt(a.gone)} biggest`} gone` : a.preventedMax ? `up to ${compact(a.preventedMax)} prevented` : 'none prevented yet'}`}
        />
        <Tile
          label={`Line overloads across the ${fmt(r.sites_total)} tested sites`}
          before={fmt(s.line_overloads_before)}
          after={fmt(s.line_overloads_after)}
          bNum={s.line_overloads_before}
          aNum={s.line_overloads_after}
          note={`${with_}: ${fmt(a.overloads)}`}
        />
      </dl>
    </section>
  )
}
