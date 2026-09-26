// Sources and licenses (#/views/sources): every dataset Overload uses, with its license and where it shows up,
// then the utility filings and the references the estimates cite. The list comes from GET /api/sources, which
// reads the references from the modules that use them, so it matches what the numbers actually rest on.
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { ErrorBanner, Loading } from '../../ui'

export default function SourcesTab() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let live = true
    api('/api/sources')
      .then((d) => live && (setData(d), setErr(null)))
      .catch((e) => live && setErr(e))
    return () => {
      live = false
    }
  }, [retry])

  if (err) return <ErrorBanner error={err} onRetry={() => setRetry((r) => r + 1)} />
  if (!data) return <Loading label="Loading the sources…" />
  return (
    <div className="src">
      <div className="vw-lead">
        <h1 className="vw-h2">Sources and licenses</h1>
        <p>Every dataset behind the maps and numbers, its license, and where it shows up. Then the utility filings and the references each estimate cites.</p>
      </div>
      <ul className="src__notes">
        {data.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>

      <h2 className="src__h">Datasets</h2>
      <div className="vw-tablewrap">
        <table className="src__table">
          <thead>
            <tr>
              <th scope="col">Dataset</th>
              <th scope="col">License</th>
              <th scope="col">Used for</th>
            </tr>
          </thead>
          <tbody>
            {data.datasets.map((d) => (
              <tr key={d.id}>
                <td>
                  {d.url ? (
                    <a href={d.url} target="_blank" rel="noreferrer">
                      {d.name}
                    </a>
                  ) : (
                    d.name
                  )}
                  {d.also && (
                    <>
                      {' '}
                      <a className="src__also" href={d.also} target="_blank" rel="noreferrer">
                        (code)
                      </a>
                    </>
                  )}
                </td>
                <td className="src__lic" data-label="License">
                  {d.license}
                </td>
                <td data-label="Used for">{d.used_for}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 className="src__h">Utility filings (Build together)</h2>
      <RefList rows={data.filings} />

      <h2 className="src__h">References the estimates cite</h2>
      <RefList rows={data.references} />
    </div>
  )
}

function RefList({ rows }) {
  if (!rows?.length) return <p className="src__none">None listed.</p>
  return (
    <ul className="src__refs">
      {rows.map((r) => (
        <li key={r.id}>
          <a href={r.url} target="_blank" rel="noreferrer">
            {r.name}
          </a>
          <span className="src__use">{r.used_for}</span>
        </li>
      ))}
    </ul>
  )
}
