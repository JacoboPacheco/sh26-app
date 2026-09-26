import { Field } from '../ui'
import { useOverload } from '../store'

// The state switcher in the top bar: any of the 48 state models, or the whole-U.S. map (click a state
// there to open it). Changing state clears the case, like Start over; the map flies to the new state.
export default function RegionPicker() {
  const { region, regions, setRegion } = useOverload()
  if (!regions) return null
  const sorted = [...regions].sort((a, b) => a.name.localeCompare(b.name))
  return (
    <div className="region-picker">
      <Field as="select" label="State" value={region} onChange={(e) => setRegion(e.target.value)}>
        <option value="US">Whole U.S. (click a state)</option>
        {sorted.map((r) => (
          <option key={r.code} value={r.code}>
            {r.name}
          </option>
        ))}
      </Field>
    </div>
  )
}
