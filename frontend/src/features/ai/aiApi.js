// FEATURE: the AI layer's public status — which features use Gemini, who checks each, what runs without it
// (backend/llm.py → GET /api/ai/status). No login; the numbers are the whole app's, not a person's.
import { api } from '../../api'

export const getAiStatus = () => api('/api/ai/status')
