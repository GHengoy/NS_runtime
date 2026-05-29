/**
 * API 및 WebSocket 서버 설정
 * 우선순위:
 * 1. 환경변수 VITE_API_BASE (지정된 경우)
 * 2. 런타임 호스트 자동 감지 (개발: localhost:8000, 외부IP: {IP}:8000)
 * 3. 프로덕션: 현재 호스트와 동일한 서버
 */

export const API_BASE = (() => {
  const envBase = import.meta.env.VITE_API_BASE
  if (envBase) return envBase

  // Backend always runs on port 8000; frontend may run on a different port
  const protocol = window.location.protocol
  const hostname = window.location.hostname
  return `${protocol}//${hostname}:8000`
})()

/**
 * WebSocket 베이스 URL은 HTTP 스킴을 ws로 교체합니다.
 * http://localhost:8000 → ws://localhost:8000
 * https://example.com → wss://example.com
 */
export const WS_BASE = API_BASE.replace(/^https?/, (match: string) =>
  match === 'https' ? 'wss' : 'ws'
)
