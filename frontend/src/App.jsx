import { useEffect, useState } from 'react'
import { api } from './api'
import ItemsPanel from './ItemsPanel'
import Layout from './Layout'
import { EmptyState, Loading } from './ui'
import useAuth from './useAuth'

function App() {
  // Keep this call: with VITE_DEMO_EMAIL/VITE_DEMO_PASSWORD set (frontend/.env) it signs
  // in as the seeded demo account on load, so per-user features need no login screen.
  const { user, loading } = useAuth()
  const [status, setStatus] = useState('checking...')

  useEffect(() => {
    api('/api/health')
      .then((data) => setStatus(data.status))
      .catch((err) => setStatus(`backend unreachable — ${err.message}`))
  }, [])

  return (
    <Layout title="Starter template" tagline="Example screens only — nothing here is a project yet" user={user}>
      <p className="muted">Backend status: {status}</p>
      {loading ? (
        <Loading />
      ) : user ? (
        <ItemsPanel />
      ) : (
        <EmptyState title="Not signed in">
          Set VITE_DEMO_EMAIL and VITE_DEMO_PASSWORD in frontend/.env for the demo auto-login, or render AuthForm.
        </EmptyState>
      )}
    </Layout>
  )
}

export default App
