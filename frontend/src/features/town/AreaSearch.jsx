import { useId, useMemo, useState } from 'react'
import { useOverload } from '../../store'
import { ErrorBanner, Field } from '../../ui'
import { matchAreas, peopleNum, useAreaList } from './areaStore'
import './town.css'

// Autocomplete over the region's areas (the towns its synthetic substations are named after).
// Keyboard: ↓/↑ move through the matches, Enter picks, Escape closes the list (then clears).
// onPick(area) gets an item of GET /api/areas. `selectedName`: the open area's name, shown in the
// box when it changes (e.g. picked from the most-exposed list).
export default function AreaSearch({ onPick, selectedName = '', label = 'Find your area' }) {
  const { region, grid } = useOverload()
  const list = useAreaList(region)
  const [q, setQ] = useState(selectedName || '')
  const [shownName, setShownName] = useState(selectedName)
  if (selectedName !== shownName) {
    // the open area changed from outside the box: show its name (React's "adjust state on prop change")
    setShownName(selectedName)
    setQ(selectedName || '')
  }
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const id = useId()
  const listId = `${id}-list`
  const regionName = grid?.meta?.region === region ? grid.meta.region_name : region

  const areas = list.data
  const matches = useMemo(() => (areas ? matchAreas(areas, q) : []), [areas, q])
  const shown = open && areas
  const activeId = shown && active >= 0 && active < matches.length ? `${id}-o${active}` : undefined

  function pick(a) {
    setQ(a.name)
    setOpen(false)
    setActive(-1)
    onPick?.(a)
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) {
        setOpen(true)
        setActive(e.key === 'ArrowDown' ? 0 : matches.length - 1)
        return
      }
      const n = matches.length
      if (!n) return
      setActive((i) => (e.key === 'ArrowDown' ? (i + 1) % n : (i <= 0 ? n : i) - 1))
    } else if (e.key === 'Enter') {
      if (!open || !matches.length) return
      e.preventDefault()
      pick(matches[active >= 0 ? active : 0])
    } else if (e.key === 'Escape') {
      if (open) {
        e.preventDefault()
        setOpen(false)
        setActive(-1)
      } else if (q) {
        e.preventDefault()
        setQ('')
      }
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  const hint = list.status === 'loading' ? `Loading ${regionName}'s areas…` : areas ? `${areas.length.toLocaleString('en-US')} areas in the synthetic ${regionName} model` : ''

  return (
    <div className="area-search">
      <Field
        label={label}
        id={`${id}-input`}
        hint={hint || undefined}
        type="text"
        role="combobox"
        autoComplete="off"
        spellCheck={false}
        placeholder="A town, e.g. Naples"
        aria-autocomplete="list"
        aria-expanded={!!shown}
        aria-controls={listId}
        aria-activedescendant={activeId}
        value={q}
        disabled={list.status === 'error'}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
          setActive(-1)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {list.status === 'error' && <ErrorBanner error={list.error} onRetry={list.retry} />}
      <ul id={listId} role="listbox" aria-label={`Areas in ${regionName}`} className="area-search__list" hidden={!shown}>
        {shown && matches.length > 0 && (
          <li role="presentation" className="area-search__cap">
            <span>{q ? 'Matches' : 'Largest areas'}</span>
            <span>People (estimates)</span>
          </li>
        )}
        {shown &&
          matches.map((a, i) => (
            <li
              key={a.slug}
              id={`${id}-o${i}`}
              role="option"
              aria-selected={i === active}
              aria-label={`${a.name}, about ${peopleNum(a.people)} people (estimate)`}
              className={`area-search__opt${i === active ? ' area-search__opt--on' : ''}`}
              onMouseDown={(e) => e.preventDefault()} // keep focus in the input so the click lands
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(a)}
            >
              <span className="area-search__name">{a.name}</span>
              <span className="area-search__people">{peopleNum(a.people)}</span>
            </li>
          ))}
        {shown && q && !matches.length && (
          <li role="presentation" className="area-search__none">
            No area called “{q}” in the {regionName} model. Try a nearby town.
          </li>
        )}
      </ul>
    </div>
  )
}
