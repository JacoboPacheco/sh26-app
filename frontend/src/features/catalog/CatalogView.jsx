import { useEffect, useRef } from 'react'
import CatalogList from './CatalogList'
import DataCenterCard from './DataCenterCard'
import NationalStats from './NationalStats'
import { useCatalog } from './catalogApi'
import './catalog.css'

// The whole catalog as one view: the national scoreboard, the list, and the selected campus's card
// (beside the list when there's room, above it when not). `onPlaced(entry)`: "Test it in the
// workspace" was pressed — a host shell switches to its map/workspace there.
export default function CatalogView({ onPlaced }) {
  const { selected } = useCatalog()
  const cardRef = useRef(null)
  // narrow layout (the card sits above the list): bring the picked campus's card into view
  useEffect(() => {
    const el = cardRef.current
    if (!selected || !el || getComputedStyle(el).position === 'sticky') return
    const r = el.getBoundingClientRect()
    if (r.top >= 0 && r.top < window.innerHeight * 0.5) return
    const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' })
    el.querySelector('.cat-card__name')?.focus({ preventScroll: true }) // keyboard users land on the card too
  }, [selected])
  return (
    <div className="cat-view">
      <NationalStats />
      <div className={`cat-view__body${selected ? ' cat-view__body--open' : ''}`}>
        <CatalogList />
        {selected && (
          <div className="cat-view__card" ref={cardRef}>
            <DataCenterCard onPlaced={onPlaced} />
          </div>
        )}
      </div>
    </div>
  )
}
