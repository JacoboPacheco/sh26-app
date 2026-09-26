import { useId, useState } from 'react'

// Login/signup form. Pass `login` and `signup` from useAuth().
export default function AuthForm({ login, signup }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const id = useId()

  const isSignup = mode === 'signup'

  async function onSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await (isSignup ? signup : login)(email, password)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} style={{ display: 'grid', gap: '0.75rem', maxWidth: '20rem' }}>
      <h2 style={{ margin: 0 }}>{isSignup ? 'Create an account' : 'Log in'}</h2>

      <label htmlFor={`${id}-email`}>Email</label>
      <input
        id={`${id}-email`}
        type="email"
        autoComplete="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />

      <label htmlFor={`${id}-password`}>Password</label>
      <input
        id={`${id}-password`}
        type="password"
        autoComplete={isSignup ? 'new-password' : 'current-password'}
        required
        minLength={isSignup ? 8 : undefined}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />

      {error && (
        <p role="alert" style={{ color: '#d33', margin: 0 }}>
          {error}
        </p>
      )}

      <button type="submit" disabled={busy}>
        {busy ? 'Please wait…' : isSignup ? 'Sign up' : 'Log in'}
      </button>

      <button
        type="button"
        onClick={() => {
          setMode(isSignup ? 'login' : 'signup')
          setError('')
        }}
      >
        {isSignup ? 'Have an account? Log in' : 'New here? Sign up'}
      </button>
    </form>
  )
}
