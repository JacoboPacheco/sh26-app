// Unsplash requires a visible credit linking the photographer and Unsplash.
// Props come straight from `node scripts/unsplash.mjs use <id>` output.
export default function PhotoCredit({ photographerName, photographerUrl, unsplashUrl }) {
  return (
    <small style={{ opacity: 0.75 }}>
      Photo by{' '}
      <a href={photographerUrl} target="_blank" rel="noopener noreferrer">
        {photographerName}
      </a>{' '}
      on{' '}
      <a href={unsplashUrl} target="_blank" rel="noopener noreferrer">
        Unsplash
      </a>
    </small>
  )
}
