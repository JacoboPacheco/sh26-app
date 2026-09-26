// #/preview/library — the Library over the live map. Save and Share act on the case on the map (as in
// the workspace toolbar); Open loads a scenario onto the map here instead of leaving for #/next.
import { useState } from 'react'
import LibraryPage from './LibraryPage'
import SaveButton from './SaveButton'
import ShareButton from './ShareButton'
import './library.css'

export default function Preview() {
  const [opened, setOpened] = useState(null)
  return (
    <div className="stack">
      <div className="lib-preview-bar">
        <p className="lib-fine" role="status" aria-live="polite">
          {opened ? `Opened “${opened}” on the map.` : 'The toolbar buttons act on the case on the map.'}
        </p>
        <div className="row">
          <SaveButton />
          <ShareButton />
        </div>
      </div>
      <LibraryPage onOpen={(sc) => setOpened(sc.name)} />
    </div>
  )
}
