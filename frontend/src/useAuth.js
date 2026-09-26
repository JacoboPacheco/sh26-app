import { useCallback, useEffect, useState } from 'react'
import { api, clearToken, getToken, login as apiLogin, signup as apiSignup } from './api'

// Demo identity: when set (VITE_DEMO_EMAIL/VITE_DEMO_PASSWORD, matching backend/seed.py),
// the app signs in as the demo account automatically, so features that need "who" (saves,
// likes, history) work without ever showing a login screen. It's a public demo account,
// not a secret — that's why it's allowed in frontend env.
const DEMO_EMAIL = import.meta.env.VITE_DEMO_EMAIL
const DEMO_PASSWORD = import.meta.env.VITE_DEMO_PASSWORD

// Current user + auth actions. `user` is null when logged out, undefined while checking.
export default function useAuth() {
  const [user, setUser] = useState(getToken() || DEMO_EMAIL ? undefined : null)

  // Always resolves; never updates state synchronously (keeps the effect below lint-clean).
  const refresh = useCallback(() => {
    const me = () => api('/api/auth/me')
    // Demo identity: log in, and if the account doesn't exist yet (fresh database,
    // seed not run), create it — so the demo self-heals instead of showing a logged-out page.
    const demoLogin = () =>
      apiLogin(DEMO_EMAIL, DEMO_PASSWORD).catch(() => apiSignup(DEMO_EMAIL, DEMO_PASSWORD))
    const start = getToken()
      ? me().catch(() => (DEMO_EMAIL && DEMO_PASSWORD ? demoLogin().then(me) : Promise.reject()))
      : DEMO_EMAIL && DEMO_PASSWORD
        ? demoLogin().then(me)
        : Promise.reject(new Error('logged out'))
    return start.then((u) => setUser(u)).catch(() => setUser(null))
  }, [])

  useEffect(() => {
    refresh()
    const expired = () => setUser(null)
    window.addEventListener('auth:expired', expired)
    return () => window.removeEventListener('auth:expired', expired)
  }, [refresh])

  const login = async (email, password) => {
    await apiLogin(email, password)
    await refresh()
  }

  const signup = async (email, password) => {
    await apiSignup(email, password)
    await refresh()
  }

  const logout = () => {
    clearToken()
    setUser(null)
  }

  return { user, loading: user === undefined, login, signup, logout }
}
