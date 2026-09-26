// FEATURE: Fix it (owned by the fix track). A one-line reminder, for any panel outside Fix it, that the
// case on screen includes line upgrades — so "No line over limit." never hides that the grid was
// reinforced. Renders nothing when there are none. Mount: <UpgradesNote /> (e.g. in CampusPanel).
import { useOverload } from '../../store'
import './fix.css'

export default function UpgradesNote() {
  const { upgrades, setUpgrades, setMode, mode } = useOverload()
  const n = Object.keys(upgrades || {}).length
  if (!n || mode === 'fix') return null
  return (
    <p className="fix-upnote" role="note">
      <span className="fix-upnote__dot" aria-hidden="true" />
      <span>
        With {n} line {n === 1 ? 'upgrade' : 'upgrades'} from{' '}
        <button type="button" className="fix-link" onClick={() => setMode('fix')}>
          Fix it
        </button>
        .{' '}
        <button type="button" className="fix-link" onClick={() => setUpgrades({})}>
          Remove {n === 1 ? 'it' : 'them'}
        </button>
      </span>
    </p>
  )
}
