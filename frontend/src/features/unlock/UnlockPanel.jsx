// FEATURE: Strengthen the grid is a full page now (StrengthenPage.jsx: App renders it while mode === 'unlock',
// in place of the left panel, the right panel and the bottom bar). This stays only because the left panel's mode
// list (App.jsx MODES) names a Panel for every mode; with mode 'unlock' that list is never on screen.
import { useOverload } from '../../store'
import { Button } from '../../ui'

export default function UnlockPanel() {
  const { setMode } = useOverload()
  return (
    <div className="stack panel-body">
      <p className="muted">Strengthen the grid opens as its own page.</p>
      <Button variant="secondary" onClick={() => setMode('unlock')}>
        Open Strengthen the grid
      </Button>
    </div>
  )
}
