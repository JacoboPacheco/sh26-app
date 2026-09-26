// Retire a whole fuel class: one toggle per fuel with its plant count and capacity. Pointing at a
// chip (or focusing it) lights that fuel's plants on the map, so the chips double as the legend.
import { fmt, fuelFamily, fuelLabel } from './fuels'
import { hoverFuel } from './plantsStore'

export default function FuelChips({ byFuel, retired, busy, onToggle }) {
  const fuels = Object.entries(byFuel || {})
    .filter(([, f]) => (f?.count || 0) > 0)
    .sort((a, b) => (b[1].pmax || 0) - (a[1].pmax || 0))
  if (!fuels.length) return null
  return (
    <div className="stack pl-fuels">
      <h3 className="panel-h">Retire a whole fuel</h3>
      <ul className="pl-chips" onPointerLeave={() => hoverFuel(null)}>
        {fuels.map(([fuel, f]) => {
          const on = retired.includes(fuel)
          return (
            <li key={fuel}>
              <button
                type="button"
                className={`pl-chip${on ? ' pl-chip--on' : ''}`}
                aria-pressed={on}
                disabled={busy}
                onClick={() => onToggle(fuel)}
                onPointerEnter={() => hoverFuel(fuel)}
                onFocus={() => hoverFuel(fuel)}
                onBlur={() => hoverFuel(null)}
                title={`${fmt(f.count)} ${f.count === 1 ? 'plant' : 'plants'}`}
                aria-label={`Retire all ${fuelLabel(fuel).toLowerCase()}: ${fmt(f.count)} ${f.count === 1 ? 'plant' : 'plants'}, ${fmt(f.pmax)} MW`}
              >
                <span className={`pl-swatch pl--${fuelFamily(fuel)}`} aria-hidden="true" />
                <span className="pl-chip__name">{fuelLabel(fuel)}</span>
                <span className="pl-chip__n">{fmt(f.pmax)} MW</span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
