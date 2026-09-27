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
  rank: 'Order: pairs whose filed build windows share months first, then pairs still to be built at different times, then pairs whose time has passed. Within each, a shared substation first, then the score (distance, timing, location confidence, same voltage). Pairs of other projects meeting at the same place fold under the first, each keeping its own rank.',
  kv: 'kV (kilovolts): the voltage a line or substation runs at. Higher-voltage lines carry more power over longer distances.',
  outage: 'An outage here is a planned one: the line is switched off so crews can work on it safely, and the power it carries is routed over other lines meanwhile. Two projects on the same line or station can often share one.',
  confidence:
    "Each project's ends are placed on OpenStreetMap from the place names in its filing. High: the filed name matched a mapped substation exactly; low: only a partial or nearby match, so check before relying on it.",
  centers: "Sperry's method measures between the centers of the two projects; ours also measures between their closest points, which is where crews would actually meet.",
  planning:
    "Georgia's filing gives each project a start date and a need date, which can be years apart: the work happens somewhere inside that span. DESC's dates come from the years its filing budgets money.",
  edition: (note) => note || "From DESC's 2024–2028 list: its 2026–2030 list no longer carries this project.",
}
