import { fmt } from '../geo'
import { useOverload } from '../store'

const LEVEL_NAMES = { '0.62': '3 AM load', '0.82': '9 AM load', '1.04': 'Heat wave', '1.08': 'Heat wave' }

// The scenario, written out as the pieces that are in it right now. Each piece has its own ×;
// "Start over" clears everything. This is how the features combine: they all add to one case.
export default function ScenarioBar() {
  const o = useOverload()
  const { site, mw, result, extraSites, loadFactor, trip, upgrades, setExtraSites, setLoadFactor, setTrip, setUpgrades, clearSite, resetAll, cascade, mapTool } = o
  const pieces = []
  if (site) {
    pieces.push({ key: 'site', label: `${fmt(mw)} MW data center at ${result?.sub_name ? titleCase(result.sub_name) : 'your site'}`, remove: clearSite })
  }
  if (extraSites.length) {
    const total = extraSites.reduce((a, s) => a + (Number(s.mw) || 0), 0)
    pieces.push({
      key: 'boom',
      label: `${site ? '+' : ''}${extraSites.length} AI ${extraSites.length === 1 ? 'campus' : 'campuses'} (${fmt(total)} MW)`,
      remove: () => setExtraSites([]),
    })
  }
  if (loadFactor !== 1) {
    const name = LEVEL_NAMES[loadFactor.toFixed(2)] || `${Math.round(loadFactor * 100)} % of peak load`
    pieces.push({ key: 'load', label: name, remove: () => setLoadFactor(1) })
  }
  if (trip.length) {
    pieces.push({ key: 'storm', label: `Storm: ${fmt(trip.length)} lines knocked out`, remove: () => setTrip([]) })
  }
  const nUp = Object.keys(upgrades).length
  if (nUp) {
    pieces.push({ key: 'fix', label: `${nUp} line ${nUp === 1 ? 'upgrade' : 'upgrades'}`, remove: () => setUpgrades({}) })
  }
  const busy = pieces.length > 0 || !!cascade || !!mapTool
  return (
    <div className="scenario-bar" role="region" aria-label="Your scenario">
      <span className="scenario-bar__label">Your scenario</span>
      {pieces.length === 0 ? (
        <span className="scenario-bar__empty">Nothing added yet: drop a data center, bring a storm, or change the time of day.</span>
      ) : (
        <ul className="scenario-bar__pieces">
          {pieces.map((pc) => (
            <li key={pc.key} className="piece">
              <span>{pc.label}</span>
              <button type="button" className="piece__x" onClick={pc.remove} aria-label={`Remove ${pc.label}`}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="scenario-bar__reset" onClick={resetAll} disabled={!busy}>
        Start over
      </button>
    </div>
  )
}

function titleCase(name) {
  return String(name)
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}
