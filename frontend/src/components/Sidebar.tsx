import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Settings2, Clock, ScanLine, ImageDown, Database, KeyRound } from 'lucide-react'

const navItems = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/collection', icon: ImageDown, label: 'Data Collection' },
  { to: '/lines', icon: Settings2, label: 'Line Manager' },
  { to: '/history', icon: Clock, label: 'Defect History' },
  { to: '/settings', icon: Database, label: 'Storage' },
  { to: '/admin', icon: KeyRound, label: 'Admin' },
]

export default function Sidebar() {
  const [expanded, setExpanded] = useState(false)

  return (
    <aside
      onClick={() => setExpanded(p => !p)}
      className="border-r border-gray-700/50 flex flex-col shrink-0 overflow-hidden cursor-pointer"
      style={{
        width: expanded ? 224 : 56,
        transition: 'width 0.22s ease',
        backgroundColor: 'rgba(33,37,43,0.97)',
        backdropFilter: 'blur(16px)',
      }}
    >
      {/* 로고 */}
      <div className="py-4 border-b border-gray-700/40" style={{ padding: '16px 12px' }}>
        <div className={`flex items-center gap-3 ${expanded ? '' : 'justify-center'}`}>
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
            <ScanLine size={16} className="text-white" />
          </div>
          <div
            className="min-w-0 overflow-hidden"
            style={{ opacity: expanded ? 1 : 0, transition: 'opacity 0.15s ease' }}
          >
            <p className="text-sm font-bold text-white leading-tight whitespace-nowrap">AI Vision System</p>
            <p className="text-xs text-gray-500 leading-tight whitespace-nowrap">Inspection System</p>
          </div>
        </div>
      </div>

      {/* 네비게이션 */}
      <nav className="flex-1 p-2 space-y-0.5">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-2 py-2.5 rounded-lg text-sm transition-colors ${
                expanded ? '' : 'justify-center'
              } ${
                isActive
                  ? 'bg-blue-500/10 text-blue-400 font-medium'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-white/5'
              }`
            }
          >
            <Icon size={16} className="shrink-0" />
            <span
              className="overflow-hidden whitespace-nowrap"
              style={{ opacity: expanded ? 1 : 0, transition: 'opacity 0.15s ease' }}
            >
              {label}
            </span>
          </NavLink>
        ))}
      </nav>

      {/* 하단 영역 */}
      <div className="border-t border-gray-700/40">
        {/* NS AI 브랜드 */}
        <div
          className="flex justify-center"
          style={{ padding: expanded ? '16px 12px 8px' : '16px 0 8px' }}
        >
          <p
            className="font-black text-gray-700 select-none"
            style={{
              writingMode: expanded ? 'horizontal-tb' : 'vertical-rl',
              letterSpacing: expanded ? '0.15em' : '0.02em',
              whiteSpace: 'nowrap',
              fontSize: expanded ? '12px' : '25px',
            }}
          >
            NS AI
          </p>
        </div>
        {/* 버전 */}
        <div style={{ padding: '0 12px 16px' }}>
          <p
            className="text-xs text-gray-700 whitespace-nowrap"
            style={{ opacity: expanded ? 1 : 0, transition: 'opacity 0.15s ease' }}
          >
            v0.1.0 · FastAPI + React
          </p>
        </div>
      </div>
    </aside>
  )
}
