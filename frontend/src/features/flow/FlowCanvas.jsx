// FEATURE: living grid (owned by the flow track).
// Contract: default export FlowCanvas({ svgRef, gRef }) renders an absolutely positioned <canvas>
// over the map (pointer-events: none) that animates electricity flowing along every drawn branch.
// Read signed flows from useOverload().view.flow (MW per branch, from -> to positive, aligned with
// grid.branches); map units -> screen pixels via gRef.current.getScreenCTM() every frame (this
// follows the camera even mid-transition). Mounted by App.jsx through GridMap's `overlay`.
export default function FlowCanvas() {
  return null
}
