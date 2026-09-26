// FEATURE: living grid — what the map shows (owned by the flow track; this basic version is the fallback).
// Shown in the impact column before anything is dropped.
export default function MapLegend() {
  return (
    <div className="map-legend stack">
      <p>
        Each light is a substation, sized by the homes and businesses it serves. Lines are high-voltage
        transmission, colored by how close they run to their limit. The only data center is the one you drop.
      </p>
    </div>
  )
}
