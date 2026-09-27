// OTHER SITES FOR THIS SIZE: Fix it's best sites, moved with the rest of Fix it to Strengthen's incident stage
// (user, Sat 22:30: "fix it should be somewhere else, like in strengthen"). The same screening table the Fix it panel
// had (GET /api/best-sites: one power-flow solve per town, connect voltage, room before the first overload, the
// busiest line the campus loads up, a plain 0-100 score whose formula the backend sends), cut to a short list. A row
// (or its town button, for the keyboard) calls `onPick(site)`: the stage puts the campus there on Watch it fail.
// When no substation takes the size, the roomiest towns with the upgrades each would need.
// Fetched only while the stage shows it (a click away: LAZY outside Florida), cached per size, load level and state.
import { fmt } from '../../geo'
import { ErrorBanner, Loading } from '../../ui'
import './fix.css'
import { prettyName, shownSites, useBestSites } from './fixStore'

/**
 *   mw, loadFactor, region   the campus size and the case's load level and state (the table is for those)
 *   onPick(site)             a row was picked: {lat, lon, town, sub, …} from /api/best-sites
 *   limit                    rows shown (the backend ranks up to 10)
 */
export default function OtherSites({ mw, loadFactor = 1, region, onPick, limit = 5 }) {
  const best = useBestSites(mw, loadFactor, !!mw, region)
  if (best.status === 'error') return <ErrorBanner error={best.error} onRetry={best.retry} />
  if (best.status !== 'done') return <Loading label="Ranking substations for this size…" />
  const { list: all, fits } = shownSites(best.data)
  const list = all.slice(0, limit)
  const level = Math.round(loadFactor * 100)
  const at = level !== 100 ? ` at ${level} % of normal demand` : ''
  // lines some state models already run over their limit with no campus at all: the screen skips them
  const pre = best.data?.strain_alone?.over || 0
  const preNote = pre ? ` (${fmt(pre)} ${pre === 1 ? 'line is' : 'lines are'} already over with no campus)` : ''
  if (!list.length) return <p className="fix-intro">No substation in this state model takes {fmt(mw)} MW{at}.</p>
  return (
    <div className="stack fix-other">
      {fits ? (
        <p className="fix-intro">
          {best.data.screened ? `${fmt(best.data.screened)} towns screened` : 'Towns screened'} with a full power-flow solve{at}: each of these takes all {fmt(mw)} MW with{' '}
          {pre ? 'no new line' : 'no line'} over its limit{preNote}, no upgrades. The best {fmt(list.length)} by score:
        </p>
      ) : (
        <p className="fix-intro">
          No substation takes {fmt(mw)} MW before {pre ? 'another' : 'a'} line overloads{at}{preNote}. The most any one takes is <strong>{fmt(best.data.max_headroom_mw)} MW</strong>.
          Where the smallest upgrades would carry it:
        </p>
      )}
      <SitesTable list={list} fits={fits} mw={mw} onPick={onPick} />
      {!fits && list.some((s) => !s.fix_calm) && <p className="fix-how">* Still over after re-rating: it needs a new line.</p>}
      <SharedLimit shared={best.data.shared_limit} />
      {fits && best.data.score_formula && (
        <p className="fix-how">
          <strong>How the score works.</strong> {best.data.score_formula}
        </p>
      )}
      <p className="fix-how">Pick a town to put the campus there on Watch it fail. Substations named after towns in a synthetic model, not real addresses.</p>
    </div>
  )
}

function SitesTable({ list, fits, mw, onPick }) {
  return (
    <table className={`fix-table${fits ? '' : ' fix-table--closest'}`}>
      <caption className="fix-sr">
        {fits ? `Ranked sites for a ${fmt(mw)} MW campus` : `The roomiest towns and the upgrades a ${fmt(mw)} MW campus needs there`}
      </caption>
      <thead>
        <tr>
          <th scope="col" className="fix-c-rank">
            <span className="fix-sr">Rank</span>
            <span aria-hidden="true">#</span>
          </th>
          <th scope="col" className="fix-c-town">
            Town
          </th>
          <th scope="col">kV</th>
          <th scope="col" title="Headroom: the MW this substation takes before any line goes over its limit">
            Room MW
          </th>
          <th scope="col" title="The most loaded line the campus adds flow to, as a share of its rating">
            Busiest line
          </th>
          <th scope="col">{fits ? 'Score' : 'Fix MVA'}</th>
        </tr>
      </thead>
      <tbody>
        {list.map((s) => (
          <tr key={s.sub} className="fix-row" onClick={() => onPick(s)} title={`${prettyName(s.name)} · ${fmt(s.kv)} kV${s.limiting ? ` · limited by ${s.limiting.label}` : ''}`}>
            <td className="fix-c-rank">{s.rank}</td>
            <th scope="row" className="fix-c-town">
              <button
                type="button"
                className="fix-row__btn"
                onClick={(e) => {
                  e.stopPropagation()
                  onPick(s)
                }}
                aria-label={`Put the ${fmt(mw)} MW campus at ${s.town} on Watch it fail`}
              >
                {s.town}
              </button>
            </th>
            <td>{fmt(s.kv)}</td>
            <td>
              {s.headroom_at_least ? '≥ ' : ''}
              {fmt(Math.round(s.headroom_mw))}
            </td>
            <td className={s.busiest_pct >= 90 ? 'fix-c-hot' : ''}>{s.busiest_pct != null ? `${Math.round(s.busiest_pct)} %` : '–'}</td>
            <td className="fix-c-score">
              {fits ? (
                <>
                  <span>{s.score ?? '–'}</span>
                  {s.score != null && (
                    <span className="fix-bar" aria-hidden="true">
                      <span style={{ transform: `scaleX(${s.score / 100})` }} />
                    </span>
                  )}
                </>
              ) : (
                <span className="fix-c-up" title={s.fix_calm ? undefined : 'Still over after the upgrades: it needs a new line'}>
                  +{fmt(s.fix_mva)}
                  {s.fix_calm ? '' : '*'}
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// Why many rows look alike: the same line or transformer limits them all.
function SharedLimit({ shared }) {
  if (!shared || shared.count < 3) return null
  return (
    <p className="fix-how">
      {shared.count === shared.of ? `All ${shared.of}` : `${shared.count} of these ${shared.of}`} sites are limited by the same element, {shared.label}, which is why
      they rank alike.
    </p>
  )
}
