// The incident briefing, for the app shells:
//   ReviewStage  the full-screen presentation over the map ({body, onClose, autoPlay, startView, startAsk, loadReplay})
//   ReviewCard   the way in after a cascade (ImpactPanel mounts it as features/bulletin/Bulletin)
//   BriefRoute   #/next/brief/<hero | preset:<id> | saved scenario id>
export { default as ReviewStage } from './ReviewStage'
export { default as ReviewCard } from '../bulletin/Bulletin'
export { default as BriefRoute } from './BriefRoute'
export { HERO } from './stage'
export { default as PresentDamage } from './PresentDamage'
