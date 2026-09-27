import { useEffect } from 'react'

// Sets document.title while this component is mounted, restoring whatever it was before (ViewsPage.jsx's
// pattern, shared so every page follows it). Pass a falsy title to leave document.title alone.
export default function usePageTitle(title) {
  useEffect(() => {
    if (!title) return undefined
    const prev = document.title
    document.title = title
    return () => {
      document.title = prev
    }
  }, [title])
}
