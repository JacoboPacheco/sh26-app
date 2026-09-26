import { useOverload } from '../../store'
import { Button } from '../../ui'
import ForecastCard from './ForecastCard'
import './forecast.css'

// #/preview/forecast: the forecast card (with the size sweep) over the live app, plus shortcuts to
// the cases worth checking. The card itself reads the store's case like it will in the app.
const FORT_MYERS = [26.64, -81.87]
const ABILENE = [32.45, -99.73]

export default function Preview() {
  const o = useOverload()
  const drop = (code, [lat, lon], mw) => {
    if (o.region !== code) {
      o.setRegion(code, { place: [lat, lon], mw })
      return
    }
    o.setMw(mw)
    o.place(lat, lon)
  }
  return (
    <div className="stack">
      <div className="row">
        <Button variant="secondary" onClick={() => drop('FL', FORT_MYERS, 1500)}>
          Fort Myers, 1,500 MW
        </Button>
        <Button variant="secondary" onClick={() => drop('FL', FORT_MYERS, 500)}>
          Fort Myers, 500 MW
        </Button>
        <Button variant="secondary" onClick={() => drop('TX', ABILENE, 1200)}>
          Abilene, 1,200 MW
        </Button>
        <Button variant="secondary" aria-pressed={o.loadFactor > 1} onClick={() => o.setLoadFactor(o.loadFactor > 1 ? 1 : 1.08)}>
          Heat wave
        </Button>
        <Button variant="secondary" aria-pressed={!!o.firm} onClick={() => o.setFirm(!o.firm)}>
          Firm service
        </Button>
      </div>
      <ForecastCard />
    </div>
  )
}
