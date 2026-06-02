import { useEffect, useRef, useState, useCallback, memo } from 'react'
import { createPortal } from 'react-dom'
import { Camera, Play, Square, Settings, AlertTriangle, Loader2, GripVertical, X, ChevronLeft, ChevronRight, Images } from 'lucide-react'
import { InspectionLine, HistoryRecord } from '../types'
import * as api from '../api'
import { WS_BASE } from '../config'
import StatusBadge from './StatusBadge'

interface RejectMeta {
  reject_window_size: number
  reject_window_marks: number[]
  reject_delay_ratio?: number   // 시간 기반: 딜레이 구간 비율 (0~1)
}

// ── 정규식 ↔ 화면 표시 변환 ───────────────────────────────────────────────
/** 파일 형식 (정규식): "2026\\.06\\.03" → 화면 표시: "2026.06.03" */
const displayFormat = (regexStr: string): string => {
  if (!regexStr) return ''
  // \\ 을 . 로 변환 (마크다운 이스케이프 표시 제거)
  return regexStr.replace(/\\\./g, '.')
}

/** 화면 표시: "2026.06.03" → 파일 형식 (정규식): "2026\\.06\\.03" */
const regexFormat = (displayStr: string): string => {
  if (!displayStr) return ''
  return displayStr.replace(/\./g, '\\.')
}

/** ISO 날짜(YYYY-MM-DD) + 포맷 → 표시 문자열 */
function buildDateDisplay(iso: string, fmt: string): string {
  if (!iso) return ''
  const [year, month, day] = iso.split('-')
  if (!year || !month || !day) return ''
  return fmt.replace('YYYY', year).replace('YY', year.slice(2)).replace('MM', month).replace('DD', day)
}

/** 표시 문자열 → 정규식 */
function displayToRegex(formatted: string): string {
  return formatted.replace(/\./g, '\\.').replace(/\//g, '\\/')
}

interface Props {
  line: InspectionLine
  onToggle: (lineName: string) => void
  onSettings: (line: InspectionLine) => void
  onSwitchProduct?: (lineName: string, productName: string) => void
  onUpdateThreshold?: (lineName: string, productName: string, className: string, newValue: number) => void
  onUpdateDetectorConfig?: (lineName: string, productName: string, config: Record<string, any>) => void
  onUpdateRejectConfig?: (lineName: string, productName: string, rejectConfig: Record<string, any>) => void
  editMode?: boolean
  gridSize?: { w: number; h: number }
}

function CameraCard({ line, onToggle, onSettings, onSwitchProduct, onUpdateThreshold, onUpdateDetectorConfig, onUpdateRejectConfig, editMode = false, gridSize }: Props) {
  const { config, stats } = line
  const isRunning = stats.status === 'running'
  const isInitializing = stats.status === 'initializing'
  const isActive = isRunning || isInitializing
  const isError = stats.status === 'error'

  const [hasFrame, setHasFrame] = useState(false)
  const hasFrameRef = useRef(false)
  const [wsConnected, setWsConnected] = useState(false)
  const [reconnectTick, setReconnectTick] = useState(0)
  const [isVisible, setIsVisible] = useState(true) // Intersection Observer로 관리
  // WebSocket으로 수신한 최신 window 상태 (프레임마다 업데이트)
  const [rejectMeta, setRejectMeta] = useState<RejectMeta>({
    reject_window_size: stats.reject_window_size ?? 0,
    reject_window_marks: stats.reject_window_marks ?? [],
  })
  const [showProductDropdown, setShowProductDropdown] = useState(false)
  const [showThresholdPanel, setShowThresholdPanel] = useState(false)
  const [panelSnapshot, setPanelSnapshot] = useState<{ ocrConfig: Record<string,any>; date: string } | null>(null)

  // 로컬 임계값 상태 (낙관적 업데이트용)
  const activeProductConfig = config.products?.[config.active_product ?? '']
  const sourceThresholds = activeProductConfig?.class_thresholds ?? config.class_thresholds
  const [localThresholds, setLocalThresholds] = useState<Record<string, number>>(sourceThresholds ?? {})

  // Defect gallery state
  // OCR mode defaults to showing the gallery; other modes require explicit opt-in
  const isOcrMode = activeProductConfig?.detector_type === 'paddleocr'
  const showGallery = !!(activeProductConfig?.show_defect_gallery ?? isOcrMode)
  const showGalleryRef = useRef(showGallery)
  useEffect(() => { showGalleryRef.current = showGallery }, [showGallery])
  const [galleryImages, setGalleryImages] = useState<HistoryRecord[]>([])
  const [galleryIndex, setGalleryIndex] = useState(0)
  const [galleryLoading, setGalleryLoading] = useState(false)
  const [galleryDefectTick, setGalleryDefectTick] = useState(0)

  // OCR 설정 편집 상태
  const [ocrConfig, setOcrConfig] = useState<Record<string, any>>(
    activeProductConfig?.detector_config ?? config.detector_config ?? {}
  )
  // 화면 표시용: 정규식 형식을 간단하게 변환
  const [editingChangeDate, setEditingChangeDate] = useState<string>(
    displayFormat(ocrConfig.change_date ?? '')
  )

  // 리젝트 타이밍 로컬 상태
  const [localRejectConfig, setLocalRejectConfig] = useState({
    time_valve_on: activeProductConfig?.time_valve_on ?? config.time_valve_on ?? 0.1,
    pre_valve_delay: activeProductConfig?.pre_valve_delay ?? config.pre_valve_delay ?? 0.25,
    trigger_delay_sec: activeProductConfig?.trigger_delay_sec ?? config.trigger_delay_sec ?? null as number | null,
    trigger_debounce_sec: activeProductConfig?.trigger_debounce_sec ?? config.trigger_debounce_sec ?? null as number | null,
  })

  // 워커 시작 시 드롭다운 자동 닫기
  useEffect(() => {
    if (isActive) setShowProductDropdown(false)
  }, [isActive])

  // Gallery: fetch defect images when gallery is enabled or product changes
  const activeProductClasses = activeProductConfig?.class_thresholds
    && Object.keys(activeProductConfig.class_thresholds).length > 0
    ? Object.keys(activeProductConfig.class_thresholds)
    : null

  const filterByProduct = (images: HistoryRecord[]) => {
    if (!activeProductClasses) return images
    return images.filter(img => activeProductClasses.includes(img.class_name))
  }

  useEffect(() => {
    if (!showGallery) return
    let cancelled = false
    const controller = new AbortController()

    const fetchGallery = async () => {
      setGalleryLoading(true)
      try {
        const images = await api.fetchDefectGallery(config.line_name, 60, controller.signal)
        if (!cancelled) {
          setGalleryImages(filterByProduct(images))
          setGalleryIndex(0)
        }
      } catch {
        // ignore abort errors
      } finally {
        if (!cancelled) setGalleryLoading(false)
      }
    }

    fetchGallery()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [showGallery, config.line_name, config.active_product]) // eslint-disable-line react-hooks/exhaustive-deps

  // Gallery: refresh when a new defect is detected via WS
  useEffect(() => {
    if (!showGallery || galleryDefectTick === 0) return
    const timer = setTimeout(async () => {
      try {
        const images = await api.fetchDefectGallery(config.line_name, 60)
        setGalleryImages(filterByProduct(images))
        setGalleryIndex(0)
      } catch {
        // ignore
      }
    }, 800)
    return () => clearTimeout(timer)
  }, [showGallery, galleryDefectTick, config.line_name, config.active_product]) // eslint-disable-line react-hooks/exhaustive-deps

  // showGallery 전환 시 img 엘리먼트가 재마운트되므로 마지막 프레임 재적용
  useEffect(() => {
    if (urlRef.current && imgRef.current) {
      imgRef.current.src = urlRef.current
    }
  }, [showGallery])

  // Intersection Observer: 화면에 보이는 카메라만 스트리밍
  // (스크롤할 때 보이지 않는 카메라의 WebSocket을 종료해 대역폭 절약)
  useEffect(() => {
    if (!cardRef.current) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsVisible(entry.isIntersecting)
      },
      { threshold: 0.1 } // 카드의 10% 이상이 보이면 visible
    )

    observer.observe(cardRef.current)

    return () => {
      observer.disconnect()
    }
  }, [])

  // config 갱신 시 로컬 임계값 동기화 (실제 변경이 있을 때만)
  useEffect(() => {
    const src = config.products?.[config.active_product ?? '']?.class_thresholds
      ?? config.class_thresholds
    const newThresholds = src ?? {}

    // 실제로 값이 변경되었을 때만 업데이트 (불필요한 리셋 방지)
    if (JSON.stringify(newThresholds) !== JSON.stringify(localThresholds)) {
      setLocalThresholds(newThresholds)
    }
  }, [config.active_product, config.products, config.class_thresholds])

  // OCR 설정 동기화 (detector_config가 변경될 때만)
  useEffect(() => {
    const newOcrConfig = activeProductConfig?.detector_config ?? config.detector_config ?? {}
    if (JSON.stringify(newOcrConfig) !== JSON.stringify(ocrConfig)) {
      setOcrConfig(newOcrConfig)
    }
  }, [config.active_product, config.products, config.detector_config])

  // Change Date 표시 동기화 (ocrConfig.change_date가 변경될 때)
  useEffect(() => {
    const newDisplayFormat = displayFormat(ocrConfig.change_date ?? '')
    if (newDisplayFormat !== editingChangeDate) {
      setEditingChangeDate(newDisplayFormat)
    }
  }, [ocrConfig.change_date])

  const navigateDate = (direction: 1 | -1) => {
    // 현재 표시된 날짜가 없으면 아무것도 안 함
    if (!editingChangeDate) return

    const fmt = (ocrConfig.date_format as string | undefined) ?? 'YYYY.MM.DD'

    // editingChangeDate → ISO(YYYY-MM-DD) 역파싱
    const sep = fmt.includes('/') ? '/' : fmt.includes('.') ? '.' : null
    const dispParts = sep ? editingChangeDate.split(sep) : null
    const fmtParts  = sep ? fmt.split(sep) : null
    let isoDate = ''
    if (dispParts && fmtParts && dispParts.length === 3 && fmtParts.length === 3) {
      let y = '', m = '', d = ''
      fmtParts.forEach((f, i) => {
        if (f === 'YYYY') y = dispParts[i]
        else if (f === 'YY') y = '20' + dispParts[i]
        else if (f === 'MM') m = dispParts[i]
        else if (f === 'DD') d = dispParts[i]
      })
      if (y && m && d) isoDate = `${y}-${m.padStart(2,'0')}-${d.padStart(2,'0')}`
    }

    // 파싱 실패 시 중단
    if (!isoDate) return

    const date = new Date(isoDate + 'T00:00:00')
    if (isNaN(date.getTime())) return
    date.setDate(date.getDate() + direction)

    const newIso = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    const formatted = buildDateDisplay(newIso, fmt)
    const newOcrConfig = { ...ocrConfig, date_value: newIso, change_date: displayToRegex(formatted) }
    setOcrConfig(newOcrConfig)
    setEditingChangeDate(formatted)
    // 화살표는 미리보기만 — Enter 또는 blur 시 저장
  }

  const [rejectFlash, setRejectFlash] = useState(false)

  const handleManualReject = useCallback(async () => {
    if (!isRunning) return
    try {
      await api.manualReject(config.line_name)
      setRejectFlash(true)
      setTimeout(() => setRejectFlash(false), 400)
    } catch (e) {
      console.error('Manual reject failed:', e)
    }
  }, [isRunning, config.line_name])

  const urlRef = useRef<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cardRef = useRef<HTMLDivElement | null>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)

  useEffect(() => {
    // 정지/에러 상태이거나 화면에 안 보이면 스트림 정리
    if (!isActive || !isVisible) {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      // urlRef는 해제하지 않음 — 마지막 프레임을 화면에 frozen으로 유지
      setWsConnected(false)
      if (!isActive) {
        setRejectMeta({ reject_window_size: 0, reject_window_marks: [] })
      }
      return
    }

    // 이미 연결 중이면 재연결 안 함
    if (wsRef.current) return

    const ws = new WebSocket(`${WS_BASE}/ws/${encodeURIComponent(config.line_name)}`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => setWsConnected(true)

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        const blob = new Blob([event.data], { type: 'image/jpeg' })
        const newUrl = URL.createObjectURL(blob)
        if (urlRef.current) URL.revokeObjectURL(urlRef.current)
        urlRef.current = newUrl
        if (imgRef.current) imgRef.current.src = newUrl  // direct DOM, no re-render
        if (!hasFrameRef.current) {
          hasFrameRef.current = true
          setHasFrame(true)  // one-time transition: loading → streaming
        }
      } else {
        try {
          const meta: RejectMeta = JSON.parse(event.data as string)
          if ((meta as any).is_defect) {
            setIsRejectActive(true)
            if (rejectOffTimer.current) clearTimeout(rejectOffTimer.current)
            rejectOffTimer.current = setTimeout(() => setIsRejectActive(false), 800)
            const dm = meta as any
            if (dm.defect_image_url) {
              // Direct insert: always update gallery state when image URL is available
              const newRecord: HistoryRecord = {
                id: `ws-${Date.now()}`,
                category: dm.defect_category || 'defect',
                line_name: config.line_name,
                class_name: (dm.defect_class || 'unknown').replace(/^text:/, ''),
                confidence: dm.defect_conf ?? 0,
                timestamp: dm.defect_ts || new Date().toISOString().slice(0, 19),
                date: (dm.defect_ts || new Date().toISOString()).slice(0, 10),
                detector_type: dm.detector_type || '',
                image_url: dm.defect_image_url,
                mark_url: dm.defect_mark_url || null,
              }
              setGalleryImages(prev => [newRecord, ...prev].slice(0, 30))
              setGalleryIndex(0)
            } else {
              setGalleryDefectTick(t => t + 1)
            }
          }
          setRejectMeta(prev => {
            if (
              prev.reject_window_size === meta.reject_window_size &&
              prev.reject_delay_ratio === meta.reject_delay_ratio &&
              prev.reject_window_marks.length === meta.reject_window_marks.length &&
              prev.reject_window_marks.every((v, i) => v === meta.reject_window_marks[i])
            ) {
              return prev  // skip re-render if data unchanged
            }
            return meta
          })
        } catch {
          // ignore parse errors
        }
      }
    }

    ws.onclose = () => {
      setWsConnected(false)
      wsRef.current = null
      reconnectTimerRef.current = setTimeout(() => setReconnectTick(t => t + 1), 1000)
    }

    ws.onerror = () => ws.close()

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      ws.close()
      wsRef.current = null
      setWsConnected(false)
    }
  }, [isActive, isVisible, config.line_name, reconnectTick])

  const { reject_window_size: winSize, reject_window_marks: winMarks, reject_delay_ratio: delayRatio } = rejectMeta

  const [isRejectActive, setIsRejectActive] = useState(false)
  const rejectOffTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const borderColor = editMode
    ? 'border-blue-500/50'
    : isRejectActive
    ? 'border-red-500 border-2'
    : isRunning
    ? 'border-emerald-400/60'
    : isError
    ? 'border-red-500/20'
    : 'border-gray-600/50 hover:border-gray-500/70'
  const rejectPositions = config.reject_positions ?? 1
  // 실행 중일 때만 바 표시. winSize는 백엔드에서 정확한 값을 보냄.
  const displayWinSize = isRunning ? (winSize > 0 ? winSize : 0) : 0

  const handleThresholdChange = (className: string, delta: number) => {
    const current = localThresholds[className] ?? 0.5
    const next = Math.round((current + delta) * 100) / 100  // 부동소수점 오차 방지
    const clamped = Math.max(0, Math.min(1, next))

    setLocalThresholds(prev => ({ ...prev, [className]: clamped }))  // 낙관적 업데이트
    if (config.active_product) {
      onUpdateThreshold?.(config.line_name, config.active_product, className, clamped)
    }
  }

  // 더블클릭 전체화면 토글
  const [isFullscreen, setIsFullscreen] = useState(false)
  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', handler)
    return () => document.removeEventListener('fullscreenchange', handler)
  }, [])
  const toggleFullscreen = () => {
    if (!cardRef.current) return
    if (document.fullscreenElement) {
      document.exitFullscreen()
    } else {
      cardRef.current.requestFullscreen()
    }
  }

  return (
    <div
      ref={cardRef}
      tabIndex={0}
      onKeyDown={e => {
        if (e.key === 'r' || e.key === 'R') {
          e.preventDefault()
          handleManualReject()
        }
        if (e.key === 'Escape' && isFullscreen) {
          document.exitFullscreen()
        }
      }}
      className={`group h-full relative border rounded-xl overflow-hidden transition-all duration-300 outline-none ${borderColor} ${isRunning && !editMode && !isRejectActive ? 'card-running-glow' : ''}`}
      style={{ backgroundColor: 'rgba(0,0,0,0.85)' }}
    >
      {/* 카메라 피드 영역 — showGallery=true 시 좌우 분할, 아니면 전체 채움 */}
      {showGallery ? (
        <div className="absolute inset-0 flex flex-col" onDoubleClick={toggleFullscreen}>
          {/* Top: Camera feed */}
          <div className="relative flex-1 min-h-0 w-full flex items-center justify-center overflow-hidden">
            <img
              ref={imgRef}
              alt="live feed"
              className={`w-full h-full object-contain${hasFrame ? '' : ' hidden'}`}
            />
            {isActive && !hasFrame && (
              <div className="text-center">
                <Loader2 size={28} className="text-gray-600 mx-auto mb-2 animate-spin" />
                <p className="text-xs text-gray-600">
                  {isInitializing
                    ? stats.init_stage === 'Streaming'
                      ? 'Streaming started'
                      : `Initializing${stats.init_stage ? ` (${stats.init_stage})` : ''}...`
                    : wsConnected ? 'Waiting for frames…' : 'Connecting…'}
                </p>
                {isInitializing && (stats.init_total ?? 0) > 0 && (
                  <div className="mt-2 w-32 mx-auto">
                    <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-amber-500 transition-all duration-300"
                        style={{ width: `${((stats.init_current ?? 0) / stats.init_total!) * 100}%` }}
                      />
                    </div>
                    <p className="text-[10px] text-gray-700 mt-1">
                      Step {stats.init_current}/{stats.init_total}
                    </p>
                  </div>
                )}
              </div>
            )}
            {!isActive && hasFrame && (
              <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
                {isError ? (
                  <div className="bg-black/70 rounded-lg px-3 py-2 text-center max-w-[80%]">
                    <AlertTriangle size={20} className="text-red-500/80 mx-auto mb-1" />
                    <p className="text-[10px] text-red-400/80 line-clamp-2">{stats.last_error || 'Error'}</p>
                  </div>
                ) : (
                  <div className="bg-black/70 rounded-lg px-3 py-2 flex items-center gap-1.5">
                    <Camera size={14} className="text-gray-500" />
                    <p className="text-[10px] text-gray-500">Offline</p>
                  </div>
                )}
              </div>
            )}
            {!isActive && !hasFrame && (
              isError ? (
                <div className="text-center px-4">
                  <AlertTriangle size={32} className="text-red-500/50 mx-auto mb-2" />
                  <p className="text-xs text-red-400/70 line-clamp-3">{stats.last_error}</p>
                </div>
              ) : (
                <div className="text-center">
                  <Camera size={36} className="text-gray-800 mx-auto mb-2" />
                  <p className="text-xs text-gray-700">Offline</p>
                </div>
              )
            )}
          </div>

          {/* Bottom: Defect image panel */}
          <div className="flex-1 min-h-0 w-full relative bg-black/30 border-t border-gray-700/40 flex items-center justify-center overflow-hidden">
            {/* Nav strip — top of gallery panel, above hover overlay reach */}
            <div className="absolute top-0 left-0 right-0 flex items-center justify-between px-1.5 bg-black/80 border-b border-gray-700/40 z-30" style={{ height: 28 }} onDoubleClick={e => e.stopPropagation()}>
              {galleryImages.length > 0 ? (
                <>
                  <button
                    onClick={() => setGalleryIndex(i => Math.min(i + 1, galleryImages.length - 1))}
                    disabled={galleryIndex >= galleryImages.length - 1}
                    className="w-6 h-6 flex items-center justify-center text-gray-500 hover:text-gray-200 disabled:opacity-20 transition-colors"
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <div className="flex flex-col items-center min-w-0 flex-1 px-1">
                    <span className="text-[9px] font-semibold text-red-400 truncate max-w-full">
                      {galleryImages[galleryIndex].class_name}
                    </span>
                    <span className="text-[9px] text-white font-mono">
                      {(galleryImages[galleryIndex].timestamp ?? '').replace('T', ' ').slice(5, 19)} · {(galleryImages[galleryIndex].confidence * 100).toFixed(1)}% · {galleryIndex + 1}/{galleryImages.length}
                    </span>
                  </div>
                  <button
                    onClick={() => setGalleryIndex(i => Math.max(i - 1, 0))}
                    disabled={galleryIndex <= 0}
                    className="w-6 h-6 flex items-center justify-center text-gray-500 hover:text-gray-200 disabled:opacity-20 transition-colors"
                  >
                    <ChevronRight size={14} />
                  </button>
                </>
              ) : (
                <span className="text-[9px] text-gray-700 mx-auto">Recent Defects</span>
              )}
            </div>

            {galleryImages.length === 0 ? (
              <div className="flex flex-col items-center gap-1.5 pt-7">
                <Images size={20} className="text-gray-800" />
                <span className="text-[10px] text-gray-700">
                  {galleryLoading ? 'Loading...' : 'No defects recorded'}
                </span>
              </div>
            ) : (
              <img
                key={galleryImages[galleryIndex].mark_url || galleryImages[galleryIndex].image_url}
                src={api.historyImageUrl(galleryImages[galleryIndex].mark_url || galleryImages[galleryIndex].image_url)}
                alt="defect"
                className="w-full h-full object-contain pt-7"
                onError={e => { (e.target as HTMLImageElement).style.opacity = '0.3' }}
              />
            )}
            {galleryLoading && galleryImages.length > 0 && (
              <div className="absolute top-8 right-1">
                <Loader2 size={10} className="text-gray-600 animate-spin" />
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="absolute inset-0 flex items-center justify-center" onDoubleClick={toggleFullscreen}>
          {/* 프레임이 있으면 항상 img 표시 (활성: 라이브 / 비활성: 마지막 프레임 frozen) */}
          <img
            ref={imgRef}
            alt="live feed"
            className={`w-full h-full object-contain${hasFrame ? '' : ' hidden'}`}
          />

          {/* 활성 상태 + 아직 프레임 없음: 로딩 표시 */}
          {isActive && !hasFrame && (
            <div className="text-center">
              <Loader2 size={28} className="text-gray-600 mx-auto mb-2 animate-spin" />
              <p className="text-xs text-gray-600">
                {isInitializing
                  ? stats.init_stage === 'Streaming'
                    ? 'Streaming started'
                    : `Initializing${stats.init_stage ? ` (${stats.init_stage})` : ''}...`
                  : wsConnected ? 'Waiting for frames…' : 'Connecting…'}
              </p>
              {isInitializing && (stats.init_total ?? 0) > 0 && (
                <div className="mt-2 w-32 mx-auto">
                  <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-amber-500 transition-all duration-300"
                      style={{ width: `${((stats.init_current ?? 0) / stats.init_total!) * 100}%` }}
                    />
                  </div>
                  <p className="text-[10px] text-gray-700 mt-1">
                    Step {stats.init_current}/{stats.init_total}
                  </p>
                </div>
              )}
            </div>
          )}

          {/* 비활성 + frozen 프레임 있음: 반투명 오버레이로 상태 표시 */}
          {!isActive && hasFrame && (
            <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
              {isError ? (
                <div className="bg-black/70 rounded-lg px-3 py-2 text-center max-w-[80%]">
                  <AlertTriangle size={20} className="text-red-500/80 mx-auto mb-1" />
                  <p className="text-[10px] text-red-400/80 line-clamp-2">{stats.last_error || 'Error'}</p>
                </div>
              ) : (
                <div className="bg-black/70 rounded-lg px-3 py-2 flex items-center gap-1.5">
                  <Camera size={14} className="text-gray-500" />
                  <p className="text-[10px] text-gray-500">Offline</p>
                </div>
              )}
            </div>
          )}

          {/* 비활성 + 프레임 없음: 기본 오프라인/에러 표시 */}
          {!isActive && !hasFrame && (
            isError ? (
              <div className="text-center px-4">
                <AlertTriangle size={32} className="text-red-500/50 mx-auto mb-2" />
                <p className="text-xs text-red-400/70 line-clamp-3">{stats.last_error}</p>
              </div>
            ) : (
              <div className="text-center">
                <Camera size={36} className="text-gray-800 mx-auto mb-2" />
                <p className="text-xs text-gray-700">Offline</p>
              </div>
            )
          )}
        </div>
      )}

      {/* FPS 오버레이 — 항상 표시 */}
      {isRunning && (
        <div className="absolute top-2 right-2 bg-black/60 rounded px-2 py-0.5 text-xs text-gray-300 font-mono z-10">
          {stats.fps > 0 ? `${stats.fps} FPS` : '— FPS'}
        </div>
      )}

      {/* 라인명 오버레이 — 좌상단, 항상 표시 */}
      <div className="absolute top-2 left-2 bg-black/60 rounded px-2 py-0.5 z-10">
        <span className="text-xs text-white font-semibold">{config.project_name || config.line_name}</span>
      </div>

      {/* Manual reject flash */}
      {rejectFlash && (
        <div className="absolute inset-0 bg-red-500/20 pointer-events-none flex items-center justify-center z-20">
          <span className="text-red-400 text-sm font-bold bg-black/60 rounded px-3 py-1">REJECT SENT</span>
        </div>
      )}

      {/* 편집 모드 오버레이 */}
      {editMode && (
        <div className="absolute inset-0 bg-blue-500/5 flex items-center justify-center pointer-events-none z-20">
          <div className="bg-black/50 rounded-lg px-3 py-1.5 flex items-center gap-1.5">
            <GripVertical size={14} className="text-blue-400" />
            <span className="text-xs text-blue-300 font-medium">Drag to move</span>
          </div>
          {gridSize && (
            <div className="absolute bottom-2 right-2 bg-black/60 rounded px-2 py-0.5">
              <span className="text-[10px] text-blue-300/80 font-mono">{gridSize.w}×{gridSize.h}</span>
            </div>
          )}
        </div>
      )}

      {/* 리젝트 바 */}
      {displayWinSize > 0 && !editMode && (
        <div className="absolute bottom-0 left-0 right-0 z-10 px-2 pb-1.5">
          <div
            className="relative w-full rounded-sm overflow-hidden"
            style={{ height: 6, backgroundColor: 'rgba(255,255,255,0.15)' }}
          >
            {delayRatio != null && delayRatio >= 0 ? (
              <>
                {/* Continuous: [흰색=valve delay][노란색=valve on] + 빨간 마크 이동 */}
                {delayRatio > 0 && (
                  <div className="absolute top-0 left-0 h-full" style={{
                    width: `${delayRatio * 100}%`,
                    backgroundColor: 'rgba(156,163,175,0.25)',
                    borderRight: '1px solid rgba(156,163,175,0.5)',
                  }} />
                )}
                <div className="absolute top-0 h-full" style={{
                  left: `${delayRatio * 100}%`,
                  width: `${(1 - delayRatio) * 100}%`,
                  backgroundColor: 'rgba(251,191,36,0.3)',
                }} />
              </>
            ) : (
              /* Trigger/Auto: 오른쪽 노란 영역 = reject_positions 칸 */
              <div className="absolute top-0 right-0 h-full" style={{
                width: `calc(max(10px, ${(rejectPositions / displayWinSize) * 100}%))`,
                backgroundColor: 'rgba(251,191,36,0.18)',
                borderLeft: '1px solid rgba(251,191,36,0.4)',
              }} />
            )}
            {/* 빨간 불량 마크 */}
            {winMarks.map((idx, mi) => (
              <div key={mi} className="absolute top-0 h-full" style={{
                left: `${(idx / displayWinSize) * 100}%`,
                width: `${Math.max(2, (1 / displayWinSize) * 100)}%`,
                minWidth: 2,
                backgroundColor: '#ef4444',
              }} />
            ))}
          </div>
        </div>
      )}

      {/* 하단 오버레이 — 마우스 호버 시 슬라이드업 */}
      <div
        className={`absolute bottom-0 left-0 right-0 z-20 transition-all duration-300 ${
          editMode
            ? 'opacity-0 pointer-events-none'
            : 'opacity-0 translate-y-2 group-hover:opacity-100 group-hover:translate-y-0'
        }`}
        style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.6) 60%, transparent 100%)' }}
      >
        {/* 컨트롤 영역 */}
        <div className="px-3 py-2.5">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <StatusBadge status={stats.status} />
              {/* Threshold/Change Date 버튼 */}
              <button
                onClick={() => {
                  setPanelSnapshot({ ocrConfig: { ...ocrConfig }, date: editingChangeDate })
                  setShowThresholdPanel(true)
                }}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold transition-colors border text-amber-500/80 hover:text-amber-400 hover:bg-amber-500/10 border-amber-500/25 hover:border-amber-500/50"
                title={activeProductConfig?.detector_type === 'paddleocr' ? 'Change Date Pattern' : 'Adjust Thresholds'}
              >
                <Settings size={11} />
                {activeProductConfig?.detector_type === 'paddleocr' ? 'Date' : 'Threshold'}
              </button>
            </div>
            <div className="flex items-center gap-2">
              {/* Active Product Selector */}
              {config.active_product && config.products && Object.keys(config.products).length > 1 ? (
                <div className="relative">
                  <button
                    onClick={() => !isActive && setShowProductDropdown(v => !v)}
                    disabled={isActive}
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold border transition-colors ${
                      isActive
                        ? 'bg-gray-500/15 text-gray-500 border-gray-500/30 cursor-not-allowed'
                        : 'bg-blue-500/15 text-blue-400 border-blue-500/30 hover:bg-blue-500/25'
                    }`}
                  >
                    {config.active_product}
                    <svg width="8" height="5" viewBox="0 0 10 6" className="ml-0.5">
                      <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
                    </svg>
                  </button>
                  {showProductDropdown && (
                    <div className="absolute bottom-full right-0 mb-1 z-20 bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1 min-w-[140px] max-h-60 overflow-y-auto">
                      {Object.keys(config.products).map(pName => (
                        <button
                          key={pName}
                          onClick={() => {
                            onSwitchProduct?.(config.line_name, pName)
                            setShowProductDropdown(false)
                          }}
                          className={`w-full text-left px-3 py-2 text-xs font-medium transition-colors ${
                            pName === config.active_product
                              ? 'text-blue-400 bg-blue-500/10'
                              : 'text-gray-300 hover:bg-gray-700'
                          }`}
                        >
                          {pName}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : config.active_product ? (
                <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-blue-500/15 text-blue-400 border border-blue-500/30">
                  {config.active_product}
                </span>
              ) : null}
            </div>
          </div>

          {/* 액션 버튼 */}
          <div className="flex gap-2">
            <button
              onClick={() => onToggle(config.line_name)}
              disabled={isInitializing}
              className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                isInitializing
                  ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20 opacity-60 cursor-not-allowed'
                  : isRunning
                  ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/20'
                  : isError
                  ? 'bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 border border-amber-500/20'
                  : 'bg-green-500/10 text-green-400 hover:bg-green-500/20 border border-green-500/20'
              }`}
            >
              {isActive ? <Square size={12} /> : <Play size={12} />}
              {isInitializing ? 'Starting...' : isRunning ? 'Stop' : isError ? 'Retry' : 'Start'}
            </button>
            <button
              onClick={() => onSettings(line)}
              className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-gray-800/60 transition-colors border border-gray-700/50"
              title="Settings"
            >
              <Settings size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* 임계값/날짜 조절 패널 모달 — Portal로 body에 렌더링하여 z-index 문제 방지 */}
      {showThresholdPanel && (activeProductConfig?.detector_type === 'paddleocr' || Object.keys(localThresholds).length > 0) && createPortal(
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[9999]">
          <div className="bg-gray-900 border border-amber-500/50 rounded-xl p-6 max-w-sm w-full mx-4 shadow-2xl max-h-[90vh] overflow-y-auto">
            {/* 헤더 */}
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold text-amber-100">
                {activeProductConfig?.detector_type === 'paddleocr' ? 'Date Patterns' : 'Reject Thresholds'}
              </h2>
              <button
                onClick={() => {
                  if (panelSnapshot) {
                    setOcrConfig(panelSnapshot.ocrConfig)
                    setEditingChangeDate(panelSnapshot.date)
                  }
                  setShowThresholdPanel(false)
                }}
                className="text-gray-500 hover:text-gray-300 transition-colors"
              >
                <X size={20} />
              </button>
            </div>

            {/* PaddleOCR 모드: detector_config 편집 */}
            {activeProductConfig?.detector_type === 'paddleocr' ? (
              <div className="space-y-3 mb-6">
                {/* Change Date Pattern */}
                <div className="flex flex-col gap-1.5 p-3 bg-black/40 rounded-lg border border-gray-700/50">
                  <label className="text-xs text-gray-400 font-medium">Date Pattern</label>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => navigateDate(-1)}
                      className="p-1.5 rounded text-gray-400 hover:text-white hover:bg-gray-700 transition-colors shrink-0"
                      title="Previous day"
                    >
                      <ChevronLeft size={15} />
                    </button>
                    <input
                      type="text"
                      value={editingChangeDate}
                      onChange={e => setEditingChangeDate(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Escape') {
                          if (panelSnapshot) {
                            setOcrConfig(panelSnapshot.ocrConfig)
                            setEditingChangeDate(panelSnapshot.date)
                          }
                          e.currentTarget.blur()
                        }
                      }}
                      onBlur={() => {
                        // blur 시 change_date 로컬 반영만 (저장은 Save 버튼)
                        const regexValue = regexFormat(editingChangeDate)
                        if (regexValue !== ocrConfig.change_date) {
                          setOcrConfig(prev => ({ ...prev, change_date: regexValue }))
                        }
                      }}
                      placeholder="e.g., 2026.02.28"
                      className="flex-1 min-w-0 px-2 py-2 bg-gray-800 border border-gray-600 rounded text-sm text-white placeholder-gray-500 focus:outline-none focus:border-amber-500 transition-colors text-center"
                    />
                    <button
                      type="button"
                      onClick={() => navigateDate(1)}
                      className="p-1.5 rounded text-gray-400 hover:text-white hover:bg-gray-700 transition-colors shrink-0"
                      title="Next day"
                    >
                      <ChevronRight size={15} />
                    </button>
                  </div>
                  <p className="text-xs text-gray-500">‹ › to navigate · Save to apply · Cancel/X to revert</p>
                </div>

                {/* Use Angle Detection */}
                <div className="flex items-center justify-between p-3 bg-black/40 rounded-lg border border-gray-700/50">
                  <label className="text-xs text-gray-400 font-medium">
                    Detect Rotated Text
                  </label>
                  <button
                    onClick={() => {
                      setOcrConfig(prev => ({ ...prev, use_angle_cls: !prev.use_angle_cls }))
                    }}
                    className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                      ocrConfig.use_angle_cls
                        ? 'bg-blue-500/30 text-blue-300 border border-blue-500/50'
                        : 'bg-gray-700/30 text-gray-400 border border-gray-600/50'
                    }`}
                  >
                    {ocrConfig.use_angle_cls ? 'ON' : 'OFF'}
                  </button>
                </div>

                {/* Detection Size Limit */}
                <div className="flex flex-col gap-1.5 p-3 bg-black/40 rounded-lg border border-gray-700/50">
                  <label className="text-xs text-gray-400 font-medium">
                    Detection Speed
                  </label>
                  <div className="flex gap-2">
                    {[480, 960, 1280].map(size => (
                      <button
                        key={size}
                        onClick={() => {
                          setOcrConfig(prev => ({ ...prev, det_limit_side_len: size }))
                        }}
                        className={`flex-1 px-2 py-1.5 rounded text-xs font-medium transition-colors ${
                          ocrConfig.det_limit_side_len === size
                            ? 'bg-amber-500/30 text-amber-300 border border-amber-500/50'
                            : 'bg-gray-700/30 text-gray-400 border border-gray-600/50 hover:bg-gray-700/50'
                        }`}
                      >
                        {size === 480 ? 'Fast' : size === 960 ? 'Balanced' : 'Accurate'}
                        <br />
                        <span className="text-[10px] opacity-70">{size}px</span>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Min Confidence for Normal */}
                <div className="flex flex-col gap-1.5 p-3 bg-black/40 rounded-lg border border-amber-500/30">
                  <div className="flex items-center justify-between">
                    <label className="text-xs text-amber-400 font-medium">
                      Min Confidence for Normal
                    </label>
                    <span className="text-xs font-mono text-amber-300 font-bold">
                      {Math.round((ocrConfig.min_confidence ?? 0) * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={99}
                    step={1}
                    value={Math.round((ocrConfig.min_confidence ?? 0) * 100)}
                    onChange={e => setOcrConfig(prev => ({ ...prev, min_confidence: +e.target.value / 100 }))}
                    className="w-full accent-amber-500 cursor-pointer"
                  />
                  <p className="text-[10px] text-gray-500">
                    Only OCR readings at or above this confidence can pass as normal.
                    Readings below are ignored — if none pass, the item is defect.
                  </p>
                </div>
              </div>
            ) : (
              /* 일반 모드: 값 조절 */
              <div className="space-y-3 mb-6 max-h-[40vh] overflow-y-auto pr-1">
                {Object.entries(localThresholds).map(([cls, val]) => (
                  <div key={cls} className="flex items-center justify-between p-3 bg-black/40 rounded-lg border border-gray-700/50">
                    <span className="text-sm font-medium text-amber-100">{cls}</span>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleThresholdChange(cls, -0.01)}
                        className="w-7 h-7 flex items-center justify-center rounded bg-amber-500/20 text-amber-400
                          hover:text-amber-200 hover:bg-amber-500/30 transition-colors font-bold text-sm"
                      >
                        ▼
                      </button>
                      <span className="text-sm font-mono text-amber-100 w-12 text-center font-bold">
                        {val.toFixed(2)}
                      </span>
                      <button
                        onClick={() => handleThresholdChange(cls, +0.01)}
                        className="w-7 h-7 flex items-center justify-center rounded bg-amber-500/20 text-amber-400
                          hover:text-amber-200 hover:bg-amber-500/30 transition-colors font-bold text-sm"
                      >
                        ▲
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* ── 리젝트 타이밍 설정 ── */}
            <div className="border-t border-gray-700/50 pt-4 mb-4">
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Reject Timing</h3>
              <div className="grid grid-cols-2 gap-3">
                {/* Valve Delay (left) */}
                <div className="flex flex-col gap-1 p-2.5 bg-black/40 rounded-lg border border-gray-700/50">
                  <label className="text-[10px] text-gray-500 font-medium">Valve Delay (sec)</label>
                  <input
                    type="number"
                    min={0}
                    step={0.01}
                    value={localRejectConfig.pre_valve_delay}
                    onChange={e => setLocalRejectConfig(prev => ({ ...prev, pre_valve_delay: +e.target.value }))}
                    className="w-full px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-sm text-white focus:outline-none focus:border-amber-500 transition-colors"
                  />
                </div>
                {/* Valve On Time (right) */}
                <div className="flex flex-col gap-1 p-2.5 bg-black/40 rounded-lg border border-gray-700/50">
                  <label className="text-[10px] text-gray-500 font-medium">Valve On Time (sec)</label>
                  <input
                    type="number"
                    min={0.01}
                    step={0.01}
                    value={localRejectConfig.time_valve_on}
                    onChange={e => setLocalRejectConfig(prev => ({ ...prev, time_valve_on: +e.target.value }))}
                    className="w-full px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-sm text-white focus:outline-none focus:border-amber-500 transition-colors"
                  />
                </div>
                {/* Trigger 모드 전용: Trigger Delay / Trigger Debounce */}
                {(config.collection_mode ?? 'auto') !== 'continuous' && (
                  <>
                    <div className="flex flex-col gap-1 p-2.5 bg-black/40 rounded-lg border border-gray-700/50">
                      <label className="text-[10px] text-gray-500 font-medium">Trigger Delay (sec)</label>
                      <input
                        type="number"
                        min={0}
                        step={0.001}
                        value={localRejectConfig.trigger_delay_sec ?? ''}
                        placeholder="e.g. 0.005"
                        onChange={e => setLocalRejectConfig(prev => ({ ...prev, trigger_delay_sec: e.target.value ? +e.target.value : null }))}
                        className="w-full px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-sm text-white placeholder-gray-600 focus:outline-none focus:border-amber-500 transition-colors"
                      />
                    </div>
                    <div className="flex flex-col gap-1 p-2.5 bg-black/40 rounded-lg border border-gray-700/50">
                      <label className="text-[10px] text-gray-500 font-medium">Trigger Debounce (sec)</label>
                      <input
                        type="number"
                        min={0}
                        step={0.001}
                        value={localRejectConfig.trigger_debounce_sec ?? ''}
                        placeholder="e.g. 0.0005"
                        onChange={e => setLocalRejectConfig(prev => ({ ...prev, trigger_debounce_sec: e.target.value ? +e.target.value : null }))}
                        className="w-full px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-sm text-white placeholder-gray-600 focus:outline-none focus:border-amber-500 transition-colors"
                      />
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* 저장/닫기 버튼 */}
            {activeProductConfig?.detector_type === 'paddleocr' ? (
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    if (config.active_product) {
                      // OCR 설정 저장
                      onUpdateDetectorConfig?.(
                        config.line_name,
                        config.active_product,
                        ocrConfig
                      )
                      // 리젝트 타이밍도 함께 저장
                      onUpdateRejectConfig?.(
                        config.line_name,
                        config.active_product,
                        localRejectConfig
                      )
                    }
                    setShowThresholdPanel(false)
                  }}
                  className="flex-1 py-2.5 rounded-lg bg-green-500/20 text-green-400 hover:bg-green-500/30 transition-colors font-semibold text-sm border border-green-500/30"
                >
                  Save
                </button>
                <button
                  onClick={() => {
                    if (panelSnapshot) {
                      setOcrConfig(panelSnapshot.ocrConfig)
                      setEditingChangeDate(panelSnapshot.date)
                    }
                    setShowThresholdPanel(false)
                  }}
                  className="flex-1 py-2.5 rounded-lg bg-gray-700/30 text-gray-400 hover:bg-gray-700/50 transition-colors font-semibold text-sm border border-gray-600/30"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    // 리젝트 타이밍 저장
                    if (config.active_product) {
                      onUpdateRejectConfig?.(
                        config.line_name,
                        config.active_product,
                        localRejectConfig
                      )
                    }
                    setShowThresholdPanel(false)
                  }}
                  className="flex-1 py-2.5 rounded-lg bg-green-500/20 text-green-400 hover:bg-green-500/30 transition-colors font-semibold text-sm border border-green-500/30"
                >
                  Save
                </button>
                <button
                  onClick={() => setShowThresholdPanel(false)}
                  className="flex-1 py-2.5 rounded-lg bg-gray-700/30 text-gray-400 hover:bg-gray-700/50 transition-colors font-semibold text-sm border border-gray-600/30"
                >
                  Cancel
                </button>
              </div>
            )}

          </div>
        </div>,
        document.body
      )}

    </div>
  )
}

export default memo(CameraCard, (prev, next) => {
  return (
    prev.editMode === next.editMode &&
    prev.gridSize?.w === next.gridSize?.w &&
    prev.gridSize?.h === next.gridSize?.h &&
    JSON.stringify(prev.line.stats) === JSON.stringify(next.line.stats) &&
    JSON.stringify(prev.line.config) === JSON.stringify(next.line.config)
  )
})
