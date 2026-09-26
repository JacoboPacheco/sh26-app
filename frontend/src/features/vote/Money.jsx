import { fmt } from '../../geo'

// The money, in big plain numbers: the high end of each estimated range, the range under it, and the
// formula one click away. Every figure is an estimate on a synthetic model. Red only for what is lost
// (the blackout); the rest is neutral.

function Tile({ label, big, range, meaning, lost, none }) {
  return (
    <div className="vote-tile">
      <div className="vote-tile__label">{label}</div>
      {big ? (
        <div className={`vote-big${lost ? ' vote-big--lost' : ''}`}>{big}</div>
      ) : (
        <div className="vote-big vote-big--none">{none || 'None'}</div>
      )}
      <div className="vote-tile__range">
        {big ? (
          <>
            Range {range} · estimate
          </>
        ) : (
          'estimate'
        )}
      </div>
      {meaning && <div className="vote-tile__meaning">{meaning}</div>}
    </div>
  )
}

function How({ title, line }) {
  if (!line) return null
  return (
    <details className="vote-how">
      <summary>{title}</summary>
      <p>
        <strong>Formula.</strong> {line.formula}
      </p>
      <p>
        <strong>Assumption.</strong> {line.assumption}
      </p>
      <p className="vote-how__src">
        Sources:{' '}
        {line.sources.map((s, i) => (
          <span key={s.url}>
            {i > 0 && '; '}
            <a href={s.url} target="_blank" rel="noopener noreferrer">
              {s.title}
            </a>
          </span>
        ))}
      </p>
    </details>
  )
}

export default function Money({ cost, sim }) {
  if (!cost) return <p className="vote-note">Not estimated: this proposal has no reported size.</p>
  const { blackout: b, upgrades: u, power_bill: bill, who_pays: w } = cost
  const tested = sim?.tested
  return (
    <div className="stack">
      <div className="vote-tiles">
        {tested && b && (
          <Tile
            label="Cost of a blackout, if it happened"
            big={b.big}
            range={b.range}
            lost
            none="No blackout on the model"
            meaning={b.high > 0 ? `${b.outage_label[0].toUpperCase()}${b.outage_label.slice(1)} without power for ${b.people_text} people (estimate).` : 'At this size no customer loses power on the model.'}
          />
        )}
        {tested && u && (
          <Tile
            label="Upgrades to keep every line within its limit"
            big={u.big}
            range={u.range}
            none="None needed on the model"
            meaning={u.high > 0 ? `${u.count} lines and transformers raised above their limits.` : 'No line goes over its limit at this size.'}
          />
        )}
        {bill && <Tile label="The campus’s own power bill, per year" big={bill.big} range={bill.range} meaning="What the campus itself would pay for electricity, not the community." />}
        {tested && w && (
          <Tile
            label="Per household, per month (illustrative)"
            big={w.big}
            range={w.range}
            none="Nothing to pass on"
            meaning={w.high > 0 ? `If the upgrades were spread over all ${w.households_text} ${cost.region_name} households.` : undefined}
          />
        )}
      </div>

      {cost.spread?.length > 0 && (
        <p className="vote-spread">
          <strong>Who pays changes the number.</strong> The same upgrades would be about{' '}
          {cost.spread.map((s, i) => (
            <span key={s.households}>
              {i > 0 && (i === cost.spread.length - 1 ? ', or ' : ', ')}
              <span className="vote-num">{s.text}</span> a month for each of {fmt(s.households)} households
            </span>
          ))}
          . Regulators and the utility&apos;s filed rates decide who really pays, which is why the second question below matters.
        </p>
      )}

      <div className="vote-hows">
        <How title="How the blackout cost is estimated" line={tested ? b : null} />
        <How title="How the upgrade cost is estimated" line={tested ? u : null} />
        <How title="How the power bill is estimated" line={bill} />
        <How title="How the per-household share is estimated" line={tested ? w : null} />
      </div>
      <p className="vote-note">{cost.note} Not counted: the campus’s own downtime, repairs, and new power plants.</p>
    </div>
  )
}
