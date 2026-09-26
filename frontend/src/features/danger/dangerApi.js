import { api } from '../../api'

// Danger zones (backend/danger.py): at this size, where would a campus set off the biggest blackouts?
// The backend computes every size at the nearest 50 MW and only between 50 and 5,000 MW.
export const SIZE_MIN = 50
export const SIZE_MAX = 5000
export const SIZE_STEP = 50
export const sizeFor = (mw) => Math.min(SIZE_MAX, Math.max(SIZE_MIN, Math.floor(Number(mw) / SIZE_STEP + 0.5) * SIZE_STEP))

export const getDanger = ({ mw, loadFactor = 1, region = 'FL', firm = false, limit = 25 }) => {
  const q = new URLSearchParams({
    mw: String(sizeFor(mw)),
    load_factor: String(loadFactor),
    region,
    firm: String(!!firm),
    limit: String(limit),
  })
  return api(`/api/grid/danger?${q}`)
}
