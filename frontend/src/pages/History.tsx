import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Search,
  Filter,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  X,
  Eye,
  Loader2,
  RefreshCw,
  Clock,
} from 'lucide-react'
import { fetchHistory, fetchHistoryFilters, historyImageUrl } from '../api'
import { WS_BASE } from '../config'
import type { HistoryRecord, HistoryFilters } from '../types'

const PAGE_SIZE = 60

function confidenceColor(c: number) {
  if (c >= 0.9) return 'text-red-400'
  if (c >= 0.75) return 'text-orange-400'
  return 'text-yellow-400'
}

function categoryBadge(cat: string) {
  if (cat === 'defect')
    return 'bg-red-500/80 text-white'
  return 'bg-amber-500/80 text-white'
}

export default function History() {
  // ── Filters ──
  const [search, setSearch] = useState('')
  const [filterLine, setFilterLine] = useState('')
  const [filterClass, setFilterClass] = useState('')
  const [filterDate, setFilterDate] = useState('')
  const [filterDetector, setFilterDetector] = useState('')
  const [filterCategory, setFilterCategory] = useState<'all' | 'defect' | 'borderline'>('all')
  const [sort, setSort] = useState<'newest' | 'oldest' | 'confidence_high' | 'confidence_low'>('newest')
  const [page, setPage] = useState(1)

  // ── Data ──
  const [records, setRecords] = useState<HistoryRecord[]>([])
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  const [filters, setFilters] = useState<HistoryFilters>({ lines: [], classes: [], dates: [], detector_types: [] })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // ── Modal ──
  const [selected, setSelected] = useState<HistoryRecord | null>(null)
  const [showMark, setShowMark] = useState(true)

  // ── AbortController for fetch cancellation ──
  const abortControllerRef = useRef<AbortController | null>(null)

  // ── Refs for WebSocket closure access to current filter state ──
  const filterRef = useRef({ filterCategory, filterLine, filterClass, filterDate, sort, page })
  useEffect(() => {
    filterRef.current = { filterCategory, filterLine, filterClass, filterDate, sort, page }
  }, [filterCategory, filterLine, filterClass, filterDate, sort, page])

  // ── Real-time defect push via /ws/defects ──
  useEffect(() => {
    const ws = new WebSocket(`${WS_BASE}/ws/defects`)

    let pingInterval: ReturnType<typeof setInterval> | null = null

    ws.onopen = () => {
      // keep-alive ping every 20s
      pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping')
      }, 20000)
    }

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type !== 'new_defect') return

        const f = filterRef.current
        // Apply current filters — skip if record doesn't match
        if (f.filterCategory !== 'all' && data.category !== f.filterCategory) return
        if (f.filterLine && data.line_name !== f.filterLine) return
        if (f.filterClass && data.class_name !== f.filterClass) return
        if (f.filterDate && data.date !== f.filterDate) return
        // Only inject at page 1 sorted by newest (otherwise position would be wrong)
        if (f.page !== 1 || f.sort !== 'newest') return

        const { type: _type, ...rec } = data as { type: string } & HistoryRecord
        setRecords(prev => [rec, ...prev].slice(0, PAGE_SIZE))
        setTotal(prev => prev + 1)
      } catch {}
    }

    ws.onerror = () => {}

    return () => {
      if (pingInterval) clearInterval(pingInterval)
      ws.close()
    }
  }, []) // connect once on mount

  // ── Load filters on mount ──
  useEffect(() => {
    fetchHistoryFilters()
      .then(setFilters)
      .catch(() => {})
  }, [])

  // ── Load records ──
  const loadRecords = useCallback(async () => {
    // Cancel previous request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    // Create new controller for this request
    abortControllerRef.current = new AbortController()

    setLoading(true)
    setError('')
    try {
      const res = await fetchHistory({
        category: filterCategory,
        line: filterLine || undefined,
        class_name: filterClass || undefined,
        date: filterDate || undefined,
        detector_type: filterDetector || undefined,
        page,
        page_size: PAGE_SIZE,
        sort,
      }, abortControllerRef.current.signal)
      setRecords(res.records)
      setTotal(res.total)
      setTotalPages(res.total_pages)
    } catch (e: any) {
      // Ignore abort errors
      if (e.name !== 'AbortError') {
        setError(e.message || 'Failed to load history')
        setRecords([])
      }
    } finally {
      setLoading(false)
    }
  }, [filterCategory, filterLine, filterClass, filterDate, filterDetector, page, sort])

  useEffect(() => {
    loadRecords()
    return () => {
      // Cleanup: abort pending requests on unmount
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [loadRecords])

  // Reset page when filters change
  useEffect(() => {
    setPage(1)
  }, [filterCategory, filterLine, filterClass, filterDate, filterDetector, sort])

  // Client-side search filter (on the loaded page)
  const displayed = search
    ? records.filter(
        r =>
          r.line_name.toLowerCase().includes(search.toLowerCase()) ||
          r.class_name.toLowerCase().includes(search.toLowerCase())
      )
    : records

  const sortLabel = {
    newest: 'Newest',
    oldest: 'Oldest',
    confidence_high: 'Confidence ↑',
    confidence_low: 'Confidence ↓',
  }

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-emerald-600/20 border border-emerald-500/30 rounded-lg flex items-center justify-center">
            <Clock size={18} className="text-emerald-400" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-white">Defect History</h1>
            <p className="text-xs text-gray-500">
              Browse saved defect &amp; borderline images
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => { loadRecords(); fetchHistoryFilters().then(setFilters).catch(() => {}) }}
            className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
            title="Refresh"
          >
            <RefreshCw size={14} />
          </button>
          <span className="text-sm text-gray-500">
            {total.toLocaleString()} records
          </span>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-4">
        {/* Category toggle */}
        <div className="flex bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          {(['all', 'defect', 'borderline'] as const).map(cat => (
            <button
              key={cat}
              onClick={() => setFilterCategory(cat)}
              className={`px-3 py-2 text-xs font-medium capitalize transition-colors ${
                filterCategory === cat
                  ? cat === 'defect'
                    ? 'bg-red-500/20 text-red-400'
                    : cat === 'borderline'
                    ? 'bg-amber-500/20 text-amber-400'
                    : 'bg-blue-500/20 text-blue-400'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>

        {/* Search */}
        <div className="flex-1 min-w-[200px] relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-600" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search line or class..."
            className="w-full bg-gray-900 border border-gray-800 rounded-lg pl-9 pr-4 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-gray-700"
          />
        </div>

        {/* Line filter */}
        <div className="flex items-center gap-1">
          <Filter size={14} className="text-gray-600" />
          <select
            value={filterLine}
            onChange={e => setFilterLine(e.target.value)}
            className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-300 focus:outline-none focus:border-gray-700"
          >
            <option value="">All Lines</option>
            {filters.lines.map(l => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </div>

        {/* Detector type filter */}
        {filters.detector_types.length > 0 && (
          <select
            value={filterDetector}
            onChange={e => setFilterDetector(e.target.value)}
            className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-300 focus:outline-none focus:border-gray-700"
          >
            <option value="">All Detectors</option>
            {filters.detector_types.map(dt => (
              <option key={dt} value={dt}>{dt}</option>
            ))}
          </select>
        )}

        {/* Class filter */}
        <select
          value={filterClass}
          onChange={e => setFilterClass(e.target.value)}
          className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-300 focus:outline-none focus:border-gray-700"
        >
          <option value="">All Classes</option>
          {filters.classes.map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        {/* Date filter */}
        <select
          value={filterDate}
          onChange={e => setFilterDate(e.target.value)}
          className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-300 focus:outline-none focus:border-gray-700"
        >
          <option value="">All Dates</option>
          {filters.dates.map(d => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>

        {/* Sort */}
        <select
          value={sort}
          onChange={e => setSort(e.target.value as typeof sort)}
          className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-300 focus:outline-none focus:border-gray-700"
        >
          <option value="newest">Newest</option>
          <option value="oldest">Oldest</option>
          <option value="confidence_high">Confidence ↑</option>
          <option value="confidence_low">Confidence ↓</option>
        </select>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="py-24 text-center">
            <Loader2 size={32} className="text-gray-600 mx-auto mb-3 animate-spin" />
            <p className="text-gray-500 text-sm">Loading...</p>
          </div>
        ) : error ? (
          <div className="py-24 text-center">
            <AlertTriangle size={32} className="text-red-500 mx-auto mb-3" />
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        ) : displayed.length === 0 ? (
          <div className="py-24 text-center">
            <AlertTriangle size={32} className="text-gray-800 mx-auto mb-3" />
            <p className="text-gray-600 text-sm">No defect images found.</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
            {displayed.map(d => (
              <div
                key={d.id}
                onClick={() => setSelected(d)}
                className="group bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-gray-600 transition-colors cursor-pointer"
              >
                {/* Image */}
                <div className="aspect-video bg-gray-950 relative overflow-hidden">
                  <img
                    src={historyImageUrl(d.mark_url || d.image_url)}
                    alt={d.class_name}
                    className="w-full h-full object-cover opacity-70 group-hover:opacity-90 transition-opacity"
                    loading="lazy"
                    onError={e => {
                      (e.target as HTMLImageElement).style.display = 'none'
                    }}
                  />
                  {/* Badges */}
                  <div className="absolute top-1.5 left-1.5 flex gap-1">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium uppercase ${categoryBadge(d.category)}`}>
                      {d.category}
                    </span>
                  </div>
                  {d.detector_type && (
                    <div className="absolute top-1.5 right-1.5">
                      <span className="bg-blue-900/80 text-blue-300 text-[10px] px-1.5 py-0.5 rounded font-medium uppercase">
                        {d.detector_type}
                      </span>
                    </div>
                  )}
                  <div className="absolute bottom-1.5 left-1.5">
                    <span className="bg-gray-900/80 text-white text-[10px] px-1.5 py-0.5 rounded font-medium uppercase">
                      {d.class_name}
                    </span>
                  </div>
                  {/* Hover icon */}
                  <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/20">
                    <Eye size={20} className="text-white/80" />
                  </div>
                </div>

                {/* Meta */}
                <div className="p-2.5">
                  <p className="text-xs font-medium text-gray-300 truncate">{d.line_name}</p>
                  <div className="flex items-center justify-between mt-1">
                    <span className={`text-xs font-bold ${confidenceColor(d.confidence)}`}>
                      {(d.confidence * 100).toFixed(0)}%
                    </span>
                    <span className="text-[10px] text-gray-600">
                      {new Date(d.timestamp).toLocaleTimeString('en-US', {
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                      })}
                    </span>
                  </div>
                  <p className="text-[10px] text-gray-600 mt-0.5">
                    {d.date}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4 pt-3 border-t border-gray-800">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft size={14} />
          </button>
          <div className="flex items-center gap-1">
            {/* Show page numbers around current page */}
            {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
              let pageNum: number
              if (totalPages <= 7) {
                pageNum = i + 1
              } else if (page <= 4) {
                pageNum = i + 1
              } else if (page >= totalPages - 3) {
                pageNum = totalPages - 6 + i
              } else {
                pageNum = page - 3 + i
              }
              return (
                <button
                  key={pageNum}
                  onClick={() => setPage(pageNum)}
                  className={`w-8 h-8 rounded-lg text-xs font-medium transition-colors ${
                    page === pageNum
                      ? 'bg-blue-500/20 text-blue-400'
                      : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
                  }`}
                >
                  {pageNum}
                </button>
              )
            })}
          </div>
          <button
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronRight size={14} />
          </button>
          <span className="text-[10px] text-gray-600 ml-2">
            Page {page} of {totalPages}
          </span>
        </div>
      )}

      {/* Image Detail Modal */}
      {selected && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          onClick={() => setSelected(null)}
        >
          <div
            className="relative bg-gray-900 border border-gray-700 rounded-xl shadow-2xl max-w-4xl w-full mx-4 max-h-[90vh] overflow-hidden flex flex-col"
            onClick={e => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
              <div className="flex items-center gap-3">
                <span className={`text-xs px-2 py-0.5 rounded font-medium uppercase ${categoryBadge(selected.category)}`}>
                  {selected.category}
                </span>
                <span className="text-sm font-medium text-white">{selected.line_name}</span>
                <span className="text-xs text-gray-500">{selected.class_name}</span>
              </div>
              <button
                onClick={() => setSelected(null)}
                className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            {/* Toggle original / annotated */}
            {selected.mark_url && (
              <div className="flex items-center gap-2 px-5 py-2 border-b border-gray-800">
                <button
                  onClick={() => setShowMark(true)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                    showMark ? 'bg-blue-500/20 text-blue-400' : 'text-gray-500 hover:text-gray-300'
                  }`}
                >
                  Annotated
                </button>
                <button
                  onClick={() => setShowMark(false)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                    !showMark ? 'bg-blue-500/20 text-blue-400' : 'text-gray-500 hover:text-gray-300'
                  }`}
                >
                  Original
                </button>
              </div>
            )}

            {/* Image */}
            <div className="flex-1 overflow-auto p-4 flex items-center justify-center bg-gray-950">
              <img
                src={historyImageUrl(
                  showMark && selected.mark_url ? selected.mark_url : selected.image_url
                )}
                alt={selected.class_name}
                className="max-w-full max-h-[60vh] object-contain rounded"
              />
            </div>

            {/* Info bar */}
            <div className="px-5 py-3 border-t border-gray-800 text-xs text-gray-400 space-y-1.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <span>
                    Confidence:{' '}
                    <span className={`font-bold ${confidenceColor(selected.confidence)}`}>
                      {(selected.confidence * 100).toFixed(1)}%
                    </span>
                  </span>
                  <span>Class: <span className="text-gray-200">{selected.class_name}</span></span>
                  {selected.detector_type && (
                    <span>Detector: <span className="text-blue-300">{selected.detector_type}</span></span>
                  )}
                </div>
                <div className="flex items-center gap-4">
                  <span>{selected.date}</span>
                  <span>
                    {new Date(selected.timestamp).toLocaleTimeString('en-US', {
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                    })}
                  </span>
                </div>
              </div>
              <p className="text-[11px] text-gray-500 font-mono truncate">
                {selected.id.split('/').pop()}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
