import { createContext, useContext } from 'react'

// The Build plans state (GridlockProvider.jsx fills it): read it with useGridlock().
export const GridlockContext = createContext(null)

export function useGridlock() {
  const v = useContext(GridlockContext)
  if (!v) throw new Error('useGridlock needs a <GridlockProvider> above it')
  return v
}
