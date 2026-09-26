// Every backend call goes through here so auth headers and the prod API URL
// are handled in one place. In dev VITE_API_URL is empty and Vite proxies
// /api and /uploads to the backend; in prod it's the Render URL.
const BASE = import.meta.env.VITE_API_URL || ''
const TOKEN_KEY = 'token'

// In a production build there is no dev-server proxy, so an empty BASE means
// every request would 404 on Vercel itself. Fail loudly instead of "backend unreachable".
if (import.meta.env.PROD && !BASE) {
  console.error('VITE_API_URL is not set. Add it in Vercel → Project → Settings → Environment Variables (your Render URL, no trailing slash) and redeploy.')
}

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

// Turns a backend-relative path like "/uploads/abc.png" into a usable src/href.
export const assetUrl = (path) => `${BASE}${path}`

export async function api(path, { method = 'GET', body, form } = {}) {
  if (import.meta.env.PROD && !BASE) {
    throw new Error('VITE_API_URL is not set on the frontend host — set it to the backend URL and redeploy')
  }
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let payload
  if (form) {
    payload = form
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const res = await fetch(`${BASE}${path}`, { method, headers, body: payload })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401) {
      clearToken()
      // lets useAuth drop the stale user (token expired, or the account no longer exists)
      window.dispatchEvent(new Event('auth:expired'))
    }
    throw new Error(errorMessage(data, res.status))
  }
  return data
}

// FastAPI sends `detail` as a string for HTTPException, but as a list of
// {loc, msg} objects for validation errors (422).
function errorMessage(data, status) {
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d) => `${d.loc?.at(-1) ?? 'input'}: ${String(d.msg).replace(/^Value error, /, '')}`)
      .join('; ')
  }
  if (status === 429) return 'Too many requests — wait a minute and try again'
  return `Request failed (${status})`
}

export async function login(email, password) {
  const form = new URLSearchParams({ username: email, password })
  const res = await fetch(`${BASE}/api/auth/login`, { method: 'POST', body: form })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(errorMessage(data, res.status))
  setToken(data.access_token)
  return data
}

export async function signup(email, password) {
  const data = await api('/api/auth/signup', { method: 'POST', body: { email, password } })
  setToken(data.access_token)
  return data
}

// Ask the backend's LLM helper (needs GEMINI_API_KEY on the backend; 503 otherwise).
export const ask = (prompt) => api('/api/ai/ask', { method: 'POST', body: { prompt } })

export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  return api('/api/upload', { method: 'POST', form })
}
