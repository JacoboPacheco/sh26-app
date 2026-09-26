import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { GridlockContext } from './context'
import { connect } from './gridlockApi'
import { boundsOf, nearDuplicates, projectPoints } from './format'

// State shared by the Build plans sidebar, the map and the detail card: the engine's data, the
// comparison settings, and what is selected / hovered. Everything is computed by the backend
// (/api/gridlock/*); this only fetches, merges the per-pair answers and remembers the selection.

const DEFAULT_PARAMS = {
  max_km: 40,
  window_months: 24,
  method: 'closest',
  utilities: { DESC: true, GPC: true, GTC: false, MEAG: false, DU: false },
}
const GEORGIA = ['GPC', 'GTC', 'MEAG', 'DU']

export function GridlockProvider({ children }) {
  const [conn, setConn] = useState({ status: 'loading' })
  const [projects, setProjects] = useState({ status: 'loading' })
  const [basemap, setBasemap] = useState({ status: 'loading' })
  const [sperry, setSperry] = useState({ status: 'loading' })
  const [params, setParamsState] = useState(DEFAULT_PARAMS)
  const [ov, setOv] = useState({ status: 'loading' })
  const [sel, setSel] = useState(null) // {kind: 'overlap', id, overlap} | {kind: 'project', id, back}
  const [hover, setHover] = useState(null) // {kind: 'overlap' | 'project', id}
  // the rail's view: 'opportunities' (the ranked pairs), 'pipeline', 'projects', 'setaside' (the records the checks kept
  // out) or 'sperry' (Sperry's worked example alone, the start of the story)
  const [tab, setTabState] = useState('opportunities')
  const [tierFilter, setTierFilter] = useState(null) // show only one distance tier in the list (Filters)
  const [filtersOpen, setFiltersOpen] = useState(false)
  // the pipeline section to bring into view when the pipeline opens from the funnel (an element id)
  const [pipeFocus, setPipeFocus] = useState(null)
  // after "Expand to the full filings": Sperry's six pairs stay marked at their ranks in the full list
  const [sperryMarks, setSperryMarks] = useState(false)
  // a list that unmounts mid-hover never sends pointerleave: switching tabs clears the hover
  const setTab = useCallback((t) => {
    setHover(null)
    setTabState(t)
  }, [])
  const openPipeline = useCallback(
    (section = null) => {
      setPipeFocus(section)
      setTab('pipeline')
    },
    [setTab],
  )
  const mapApi = useRef(null)
  const registerMap = useCallback((api) => {
    mapApi.current = api
  }, [])
  const client = conn.client

  // Fetchers set state only when their answer arrives (every slice starts as 'loading'); the
  // retry versions below reset to 'loading' first, from a click, never from an effect.
  const fetchConn = useCallback(
    () =>
      connect().then(
        (c) => setConn({ status: 'ready', ...c }),
        (error) => setConn({ status: 'error', error }),
      ),
    [],
  )
  useEffect(() => {
    fetchConn()
  }, [fetchConn])
  const load = useCallback(() => {
    setConn({ status: 'loading' })
    fetchConn()
  }, [fetchConn])

  const fetchProjects = useCallback(
    () =>
      client?.projects().then(
        (d) => setProjects({ status: 'ready', list: d?.projects || [], quarantine: d?.quarantine || [] }),
        (error) => setProjects({ status: 'error', error }),
      ),
    [client],
  )
  const fetchBasemap = useCallback(
    () =>
      client?.basemap().then(
        (data) => setBasemap({ status: 'ready', data }),
        (error) => setBasemap({ status: 'error', error }),
      ),
    [client],
  )
  const fetchSperry = useCallback(
    () =>
      client?.sperryCheck().then(
        (data) => setSperry({ status: 'ready', data }),
        (error) => setSperry({ status: 'error', error }),
      ),
    [client],
  )
  useEffect(() => {
    fetchProjects()
    fetchBasemap()
    fetchSperry()
  }, [fetchProjects, fetchBasemap, fetchSperry])
  const loadProjects = useCallback(() => {
    setProjects({ status: 'loading' })
    fetchProjects()
  }, [fetchProjects])
  const loadBasemap = useCallback(() => {
    setBasemap({ status: 'loading' })
    fetchBasemap()
  }, [fetchBasemap])
  const loadSperry = useCallback(() => {
    setSperry({ status: 'loading' })
    fetchSperry()
  }, [fetchSperry])

  // Overlaps: DESC against every Georgia sponsor switched on, in one engine call (it takes comma
  // lists and ranks the union). Debounced so a slider drag sends one request.
  const others = useMemo(() => GEORGIA.filter((u) => params.utilities[u]), [params.utilities])
  const pairs = useMemo(() => (params.utilities.DESC ? others.map((b) => ['DESC', b]) : []), [params.utilities.DESC, others])
  const reqId = useRef(0)
  const loadOverlaps = useCallback(() => {
    if (!client) return
    const id = ++reqId.current
    if (!pairs.length) {
      setOv({ status: 'ready', overlaps: [], total_pairs: 0, flagged: 0 })
      return
    }
    setOv((cur) => ({ ...cur, status: cur.overlaps ? 'refreshing' : 'loading' }))
    const { max_km, window_months, method } = params
    client.overlaps({ max_km, window_months, method, a: 'DESC', b: others.join(',') }).then(
      (r) => {
        if (id !== reqId.current) return
        const list = (r?.overlaps || []).map((o, i) => ({ ...o, displayRank: o.rank ?? i + 1 }))
        setOv({
          status: 'ready',
          overlaps: list,
          total_pairs: r?.total_pairs || 0,
          flagged: r?.flagged ?? list.length,
          truncated: !!r?.truncated,
          by_tier: r?.by_tier || null,
          window_assumed: r?.window_assumed || null,
          compared: r?.compared || null,
        })
      },
      (error) => id === reqId.current && setOv({ status: 'error', error }),
    )
  }, [client, pairs, others, params])
  useEffect(() => {
    if (!client) return
    const t = setTimeout(loadOverlaps, 220)
    return () => clearTimeout(t)
  }, [client, loadOverlaps])

  // the engine's settings for what is on screen (DESC x every Georgia sponsor switched on); null: nothing to compare.
  // The calendar asks with them and the downloads carry them, so a file always matches the screen.
  const engineParams = useMemo(
    () =>
      pairs.length ? { max_km: params.max_km, window_months: params.window_months, method: params.method, a: 'DESC', b: others.join(',') } : null,
    [pairs.length, params.max_km, params.window_months, params.method, others],
  )
  // the pairs as a ranked list (the default) or as the coordination calendar (the rail widens for its timeline)
  const [listView, setListViewState] = useState('list')
  const setListView = useCallback((v) => {
    setHover(null)
    setListViewState(v)
  }, [])

  const byId = useMemo(() => Object.fromEntries((projects.list || []).map((p) => [p.id, p])), [projects.list])
  // projects whose names nearly match another's: the list adds their project number
  const nearDup = useMemo(() => nearDuplicates(projects.list), [projects.list])
  const visible = useMemo(() => (projects.list || []).filter((p) => params.utilities[p.utility] ?? true), [projects.list, params.utilities])
  const overlaps = useMemo(() => (ov.overlaps || []).filter((o) => byId[o.a] && byId[o.b]), [ov.overlaps, byId])

  const setParams = useCallback((patch) => setParamsState((p) => ({ ...p, ...patch })), [])
  const toggleUtility = useCallback((u) => setParamsState((p) => ({ ...p, utilities: { ...p.utilities, [u]: !p.utilities[u] } })), [])

  // The view for a pair: centred on where the two projects come closest, wide enough to show as
  // much of both as fits within ~50 km each way (and at least ~8 km, so a touching pair has context).
  const boundsForOverlap = useCallback(
    (o) => {
      const cp = (o.closest_points || []).map(([lat, lon]) => [lon, lat])
      const all = boundsOf([...projectPoints(byId[o.a]), ...projectPoints(byId[o.b]), ...cp])
      if (!cp.length || !all) return all
      const mid = [(cp[0][0] + cp[cp.length - 1][0]) / 2, (cp[0][1] + cp[cp.length - 1][1]) / 2]
      const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
      const dx = clamp(Math.max(mid[0] - all[0], all[2] - mid[0]), 0.09, 0.55)
      const dy = clamp(Math.max(mid[1] - all[1], all[3] - mid[1]), 0.07, 0.45)
      return [mid[0] - dx, mid[1] - dy, mid[0] + dx, mid[1] + dy]
    },
    [byId],
  )

  // The sheet: the pair open over the right of the map ({id, overlap} | null), with its three steps (the
  // overlap, the two agents negotiating, the drafted agreement). Picking a pair anywhere (the list, the map,
  // the pipeline's Sperry table) opens it; the page frames the pair in the part of the map left visible.
  // `cover`: how many px of the map's right side the sheet covers (0 when closed or full screen).
  const [draft, setDraft] = useState(null)
  const [cover, setCover] = useState(0)
  const openDraft = useCallback((o) => {
    // a pair drawn with a label of its own (Sperry's OVL number at the start) opens as the engine ranked it
    const pair = o.mapRank != null ? { ...o, mapRank: undefined, displayRank: o.rank } : o
    setHover(null)
    setSel({ kind: 'overlap', id: pair.id, overlap: pair })
    setDraft((cur) => (cur?.id === pair.id ? cur : { id: pair.id, overlap: pair }))
  }, [])
  const openOverlap = openDraft
  const closeDraft = useCallback(() => {
    setDraft(null)
    setSel(null)
  }, [])
  // a project's "Where this came from": inside the sheet when one is open (Back returns to the pair), else a
  // card over the map
  const openProject = useCallback(
    (id, { fly = false } = {}) => {
      setSel((cur) => ({ kind: 'project', id, back: cur?.kind === 'overlap' ? cur : cur?.back || null }))
      const b = boundsOf(projectPoints(byId[id]))
      if (fly) mapApi.current?.flyTo(b, { card: true })
      else mapApi.current?.ensureVisible(b)
    },
    [byId],
  )
  const close = useCallback(() => setSel(null), [])
  const back = useCallback(() => setSel((cur) => cur?.back || null), [])
  const flyToOverlap = useCallback((o) => mapApi.current?.flyTo(boundsForOverlap(o), { card: false }), [boundsForOverlap])

  // Sperry's worked example as the start: the map shows only their ten projects (as the pipeline found them in the
  // filings) and their six pairs, labelled with their own numbers; "Expand to the full filings" returns to the whole
  // comparison with those six marked at their ranks.
  const sperryView = tab === 'sperry'
  const sperryMap = useMemo(() => {
    if (!sperryView || sperry.status !== 'ready') return null
    const ids = new Set((sperry.data?.projects || []).map((p) => p.our_id).filter((id) => id && byId[id]))
    const pairs = (sperry.data?.rows || [])
      .map((r) => {
        const o = r.ours?.overlap
        if (!o || !byId[o.a] || !byId[o.b]) return null
        return { ...o, mapRank: Number(String(r.overlap_id).replace(/\D+/g, '')) || null, sperry: r.overlap_id }
      })
      .filter(Boolean)
    return { projects: (projects.list || []).filter((p) => ids.has(p.id)), overlaps: pairs }
  }, [sperryView, sperry, byId, projects.list])
  const enterSperry = useCallback(() => {
    setDraft(null)
    setSel(null)
    setTab('sperry')
  }, [setTab])
  useEffect(() => {
    if (!sperryMap?.projects.length) return
    const t = setTimeout(() => mapApi.current?.flyTo(boundsOf(sperryMap.projects.flatMap(projectPoints)), { card: false }), 60)
    return () => clearTimeout(t)
  }, [sperryMap])
  // leaving their example any way ("Expand", "‹ Pairs", the rail's footer) returns the camera to both states, unless a
  // pair's sheet is open (it keeps its own framing)
  const wasSperry = useRef(false)
  const draftOpen = draft != null
  useEffect(() => {
    const left = wasSperry.current && !sperryView
    wasSperry.current = sperryView
    if (!left || draftOpen) return undefined
    const t = setTimeout(() => mapApi.current?.fit(), 60)
    return () => clearTimeout(t)
  }, [sperryView, draftOpen])
  const expandSperry = useCallback(() => {
    setSperryMarks(true)
    setTab('opportunities')
  }, [setTab])

  const value = {
    conn,
    reload: load,
    // fallback: the engine answers but its pipeline hasn't run, so it serves Sperry's worked example
    // (it says so itself)
    fallback: conn.status === 'ready' && !!conn.summary?.fallback,
    fallbackReason: conn.summary?.fallback_reason || null,
    summary: conn.summary,
    client,
    projects,
    loadProjects,
    byId,
    nearDup,
    visible,
    basemap,
    loadBasemap,
    sperry,
    loadSperry,
    params,
    setParams,
    toggleUtility,
    resetParams: () => setParamsState(DEFAULT_PARAMS),
    pairs,
    engineParams,
    listView,
    setListView,
    // the calendar is showing (the pairs view, not one of the rail's sub views): the page widens the rail
    calendarOn: listView === 'calendar' && !['pipeline', 'projects', 'setaside', 'sperry'].includes(tab),
    ov,
    overlaps,
    loadOverlaps,
    sel,
    openOverlap,
    openProject,
    close,
    back,
    draft,
    openDraft,
    closeDraft,
    flyToOverlap,
    cover,
    setCover,
    hover,
    setHover,
    tab,
    setTab,
    tierFilter,
    setTierFilter,
    filtersOpen,
    setFiltersOpen,
    pipeFocus,
    openPipeline,
    sperryView,
    sperryMap,
    enterSperry,
    expandSperry,
    sperryMarks,
    setSperryMarks,
    mapApi,
    registerMap,
  }
  return <GridlockContext.Provider value={value}>{children}</GridlockContext.Provider>
}

