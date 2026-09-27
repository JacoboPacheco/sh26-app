// SOLUTIONS, SIMPLE -> IN DEPTH (user, Sat 20:31): "The full solution" on the presentation's options opens Strengthen
// straight into the incident solution stage for this exact case (same site, size, time of day), built from the
// briefing report already computed. Its address is the stage's own deep link, built in ONE place
// (features/unlock/incident.js incidentHash: '#/strengthen?incident=' + the drop-link query, encoded), so the two
// can never drift apart.
import { incidentHash } from '../unlock/incident'

export const fullSolutionHref = incidentHash

// the national map and a plant outage have no incident stage (the briefing body can't carry the plants)
export const hasFullSolution = (caseBody) => !!caseBody && (caseBody.region || 'FL') !== 'US' && !caseBody.outages?.length
