import { api } from '../../api'

// Fix it + best sites (backend/fixit.py). `body` is a case (backend/grid.py → CaseIn).
export const findFix = (body) => api('/api/fix', { method: 'POST', body })

// `region` is optional (a two-letter state code; the backend defaults to Florida).
export const getBestSites = (mw, loadFactor = 1, limit = 10, region) => {
  const q = new URLSearchParams({ mw: String(Math.round(mw)), load_factor: String(loadFactor), limit: String(limit) })
  if (region) q.set('region', region)
  return api(`/api/best-sites?${q}`)
}
