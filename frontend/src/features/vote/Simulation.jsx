import { fmt } from '../../geo'

// What a campus of the reported size does on the synthetic grid model, flexible and firm side by side.
// People are an estimate. Red only for people who lose power.

const noAbout = (t) => String(t || '').replace(/^about /, '')

function Outcome({ o, kind }) {
  const lost = o.people > 0
  return (
    <div className="vote-outcome">
      <h3 className="vote-h3">{o.label}</h3>
      <p className="vote-outcome__meaning">{o.meaning}</p>
      <div className={`vote-big${lost ? ' vote-big--lost' : ' vote-big--none'}`}>
        {lost ? (
          <>
            <span className="vote-approx">about</span> {noAbout(o.people_text)}
          </>
        ) : (
          'No one'
        )}
      </div>
      <div className="vote-tile__range">people without power · estimate</div>
      <p className="vote-outcome__text">{o.text}</p>
      {kind === 'flexible' && o.areas?.length > 0 && (
        <p className="vote-outcome__areas">
          Where, on the model: {o.areas.map((a) => `${a.area} (${a.people_text})`).join(', ')}
          {o.area_count > o.areas.length ? ` and ${o.area_count - o.areas.length} more` : ''}.
        </p>
      )}
    </div>
  )
}

export default function Simulation({ sim, mw }) {
  if (!sim?.tested)
    return (
      <div className="stack">
        <p className="vote-note">Not tested on a grid model. {sim?.reason}</p>
        <p className="vote-note vote-note__frame">{sim?.note}</p>
      </div>
    )
  return (
    <div className="stack">
      <p className="vote-simhead">{sim.headline}</p>
      <dl className="vote-facts vote-facts--sim">
        <div>
          <dt>Tested size</dt>
          <dd>{fmt(sim.tested_mw)} MW</dd>
        </div>
        <div>
          <dt>Room at this site</dt>
          <dd>{fmt(sim.room_mw)} MW</dd>
        </div>
        <div>
          <dt>Lines and transformers over their limit</dt>
          <dd>{sim.overloaded}</dd>
        </div>
        <div>
          <dt>Connects at</dt>
          <dd>
            {sim.site.area || sim.site.substation} ({fmt(sim.site.kv)} kV)
          </dd>
        </div>
      </dl>
      <div className="vote-outcomes">
        <Outcome o={sim.flexible} kind="flexible" />
        <Outcome o={sim.firm} kind="firm" />
      </div>
      {sim.why && <p className="vote-why">{sim.why}</p>}
      {sim.lines?.length > 0 && (
        <details className="vote-how">
          <summary>
            {sim.lines.length === 1 ? 'The overloaded line on the model' : `The ${Math.min(sim.lines.length, 8)} most overloaded lines and transformers on the model`} ({fmt(sim.tested_mw || mw)} MW)
          </summary>
          <ul className="vote-plain">
            {sim.lines.map((l, i) => (
              <li key={i}>
                {l.transformer ? `${l.from} transformer` : `${l.from} to ${l.to}`} · {fmt(l.kv)} kV · {fmt(l.pct)}% of its limit
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="vote-note vote-note__frame">
        {sim.note} Model: {sim.region_name}; interconnection: {sim.interconnect}. People without power = load lost × the state&apos;s residents per MW of model load, an estimate.
      </p>
    </div>
  )
}
