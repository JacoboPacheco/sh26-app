// FEATURE: the AI layer's public status — which features use Gemini, who checks each, what runs without it, the
// checker's ledger and the power-flow validation (backend/llm.py → GET /api/ai/status, GET /api/ai/validation).
// No login; the numbers are the whole app's, not a person's.
import { api } from '../../api'

export const getAiStatus = () => api('/api/ai/status')
// every state model's flow comparison (backend/demo/validation.json), for the "every state" table
export const getAiValidation = () => api('/api/ai/validation')
