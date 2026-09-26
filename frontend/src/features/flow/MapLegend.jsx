import './flow.css'

// FEATURE: the living grid, what the map shows (flow track). ImpactPanel renders it in the impact
// column before anything is dropped. It answers the first question judges ask ("are all the dots
// data centers?"): no, every light is a substation; the only data center is the one you drop.
// Swatches mirror the map's classes in index.css (.sub, .sub--dim, .sub--dark, .ln--*, .site).
const LINES = [
  ['calm', 'Calm'],
  ['strained', 'Near its limit'],
  ['over', 'Past its limit'],
  ['tripped', 'Tripped'],
]

export default function MapLegend() {
  return (
    <section className="map-legend flow-key" aria-label="Map key">
      <h3 className="panel-h">What the map shows</h3>
      <ul className="flow-key__list">
        <li>
          <svg width="30" height="16" viewBox="0 0 30 16" aria-hidden="true">
            <circle className="fk-halo" cx="4" cy="8" r="3.4" />
            <circle className="fk-sub" cx="4" cy="8" r="1.3" />
            <circle className="fk-halo" cx="12.5" cy="8" r="4.8" />
            <circle className="fk-sub" cx="12.5" cy="8" r="1.9" />
            <circle className="fk-halo" cx="23.5" cy="8" r="6.5" />
            <circle className="fk-sub" cx="23.5" cy="8" r="2.7" />
          </svg>
          <span>
            <strong>Every light is a substation</strong>, not a data center. Bigger lights serve more homes and businesses.
          </span>
        </li>
        <li>
          <svg width="30" height="16" viewBox="0 0 30 16" aria-hidden="true">
            <rect className="fk-land" x="0" y="0" width="30" height="16" rx="3" />
            <circle className="fk-dim" cx="8.5" cy="8" r="2.4" />
            <circle className="fk-dark-halo" cx="21" cy="8" r="6.5" />
            <circle className="fk-dark" cx="21" cy="8" r="2.4" />
          </svg>
          <span>Amber: part of the area it serves lost power. Black: most or all of it did.</span>
        </li>
        {/* no dashes are drawn with reduced motion, so this row hides then (flow.css) */}
        <li className="flow-key__motion">
          <svg width="30" height="16" viewBox="0 0 30 16" aria-hidden="true">
            <line className="fk-wire" x1="1" y1="8" x2="29" y2="8" />
            <line className="fk-flow" x1="1" y1="8" x2="29" y2="8" />
          </svg>
          <span>Electricity moving. Faster, denser dashes mean a busier line.</span>
        </li>
        <li>
          <svg width="30" height="16" viewBox="0 0 30 16" aria-hidden="true">
            <circle className="fk-dc-ring" cx="15" cy="8" r="6.5" />
            <circle className="fk-dc" cx="15" cy="8" r="2.6" />
          </svg>
          <span>
            <strong>Your data center</strong>, the white ring. The map draws no others.
          </span>
        </li>
      </ul>
      <div className="flow-key__lines">
        <span>Lines, by how close they run to their limit</span>
        <ul className="flow-key__scale">
          {LINES.map(([k, label]) => (
            <li key={k}>
              <svg width="100%" height="8" viewBox="0 0 40 8" preserveAspectRatio="none" aria-hidden="true">
                <line className={`fk-ln fk-ln--${k}`} x1="2" y1="4" x2="38" y2="4" vectorEffect="non-scaling-stroke" />
              </svg>
              {label}
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
