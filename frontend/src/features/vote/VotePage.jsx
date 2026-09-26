import { useEffect, useRef } from 'react'
import BriefPage from './BriefPage'
import ProposalPage from './ProposalPage'
import SearchHome from './SearchHome'
import { useRoute } from './voteApi'
import './vote.css'

// Before the vote (#/vote): a document-style page for a resident, reporter or commissioner facing a real
// proposed data center. Search → a proposal → its printable brief. Rendered inside OverloadProvider, so
// "Watch what could happen" can open the map with the campus placed.
export default function VotePage() {
  const route = useRoute()
  const first = useRef(true)

  // the page owns the document while it is open (light print rules are scoped to this class)
  useEffect(() => {
    document.body.classList.add('vote-open')
    return () => document.body.classList.remove('vote-open')
  }, [])

  // a new page starts at the top with focus on its heading
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    window.scrollTo(0, 0)
    const t = window.setTimeout(() => document.querySelector('.vote-shell h1')?.focus({ preventScroll: true }), 60)
    return () => window.clearTimeout(t)
  }, [route.page, route.id])

  return (
    <div className={`vote-shell vote-shell--${route.page}`}>
      <header className="vote-top">
        <a className="vote-top__map" href="#/">
          <span aria-hidden="true">←</span> Overload map
        </a>
        <span className="vote-top__name">Before the vote</span>
        <span className="vote-top__syn">Synthetic grid model</span>
      </header>
      <main className="vote-main">
        {route.page === 'home' && <SearchHome key="home" initialQ={route.q} initialState={route.state} />}
        {route.page === 'proposal' && <ProposalPage key={route.id} id={route.id} />}
        {route.page === 'brief' && <BriefPage key={`brief-${route.id}`} id={route.id} />}
      </main>
    </div>
  )
}
