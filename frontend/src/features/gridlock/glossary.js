// The Build together page's terms, defined once (Gloss.jsx shows them where the words appear).
export const GLOSS = {
  flagged: (mi = 25) =>
    `Flagged: the two projects come within ${mi} miles of each other at their closest points (Sperry's cutoff is 25 mi, 40.2336 km).`,
  window:
    "Build window: the months a project is under construction. From its filing (a filed start date, or DESC's yearly spending), else the 24 months before its in-service date (marked 'start derived').",
  station: 'Same station: both filings work at the same substation, where the two systems meet, so outages and work there could be planned once.',
  groups: {
    together: 'Both build windows share months that are still ahead or open now: the two projects could be built together as filed.',
    apart: 'Both projects are still to be built, but their build windows share no months: building together would need one schedule to move.',
    unknown: 'At least one filing gives no in-service date.',
    passed: "The months they shared have passed, or one project's build window is already over, as filed. Listed for the record.",
  },
  rank: 'Order: pairs building in the same months first, then pairs still to be built at different times, then pairs whose time has passed. Within each, a shared substation first, then the score (distance, timing, location confidence, same voltage).',
  edition: (note) => note || "From DESC's 2024–2028 list: its 2026–2030 list no longer carries this project.",
}
