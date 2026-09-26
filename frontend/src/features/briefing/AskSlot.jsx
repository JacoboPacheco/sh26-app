import { Component, Suspense, lazy } from 'react'
import { Loading } from '../../ui'

// The Ask box (features/ask) inside the review stage, loaded lazily: until that track ships it — or
// if it is half-written and throws — the stage shows a short note instead of breaking.
const loaders = import.meta.glob('../ask/index.js')
const load = loaders['../ask/index.js']
const AskBox = load ? lazy(() => load().then((m) => ({ default: m.AskBox || Missing }))) : null

function Missing({ note }) {
  return <p className="muted">{note}</p>
}

class Guard extends Component {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error) {
    console.error('Ask box failed', error)
  }

  render() {
    return this.state.failed ? <p className="muted">{this.props.note}</p> : this.props.children
  }
}

export default function AskSlot({ caseBody, lang, onLangChange, inputRef, note }) {
  if (!AskBox) return <Missing note={note} />
  return (
    <Guard note={note}>
      <Suspense fallback={<Loading label="…" />}>
        <AskBox caseBody={caseBody} lang={lang} onLangChange={onLangChange} inputRef={inputRef} autoFocus />
      </Suspense>
    </Guard>
  )
}
