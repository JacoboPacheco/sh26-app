import { api } from '../../api'

// POST /api/bulletin (backend/bulletin.py): the server re-runs the case's cascade and writes a
// three-sentence public bulletin about it. -> {text, fallback, facts}
export const getBulletin = (caseBody) => api('/api/bulletin', { method: 'POST', body: caseBody })
