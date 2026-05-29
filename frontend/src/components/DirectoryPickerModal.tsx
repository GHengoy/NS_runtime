import { useState, useEffect, useCallback } from 'react'
import { X, FolderOpen, Folder, ChevronRight, Home, ArrowLeft, Check } from 'lucide-react'
import { browseFiles, FileBrowseItem } from '../api'

interface Props {
  initialPath?: string
  onSelect: (path: string) => void
  onClose: () => void
}

export default function DirectoryPickerModal({ initialPath, onSelect, onClose }: Props) {
  const [currentPath, setCurrentPath] = useState('')
  const [parentPath, setParentPath] = useState('')
  const [items, setItems] = useState<FileBrowseItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const navigate = useCallback(async (path: string) => {
    setLoading(true)
    setError('')
    try {
      const data = await browseFiles(path)
      setCurrentPath(data.current)
      setParentPath(data.parent)
      setItems(data.items.filter(i => i.is_dir))
    } catch {
      setError('Cannot open directory')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    navigate(initialPath || '')
  }, [])

  const dirs = items.filter(i => i.is_dir)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0,0,0,0.7)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-gray-700 flex flex-col"
        style={{ backgroundColor: '#1c1c1f', maxHeight: '70vh' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <FolderOpen size={16} className="text-violet-400" />
            <span className="text-sm font-medium text-white">Select Directory</span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Current path bar */}
        <div className="px-4 py-2 border-b border-gray-700/50 flex items-center gap-2">
          <button
            onClick={() => navigate('')}
            className="text-gray-500 hover:text-white transition-colors shrink-0"
            title="Home"
          >
            <Home size={14} />
          </button>
          <ChevronRight size={12} className="text-gray-600 shrink-0" />
          <span className="text-xs text-gray-400 font-mono truncate flex-1" title={currentPath}>
            {currentPath || '—'}
          </span>
          {parentPath && parentPath !== currentPath && (
            <button
              onClick={() => navigate(parentPath)}
              className="text-gray-500 hover:text-white transition-colors shrink-0"
              title="Go up"
            >
              <ArrowLeft size={14} />
            </button>
          )}
        </div>

        {/* Directory list */}
        <div className="flex-1 overflow-y-auto min-h-0">
          {loading ? (
            <div className="flex items-center justify-center py-10 text-gray-500 text-sm">
              Loading...
            </div>
          ) : error ? (
            <div className="flex items-center justify-center py-10 text-red-400 text-sm">
              {error}
            </div>
          ) : dirs.length === 0 ? (
            <div className="flex items-center justify-center py-10 text-gray-500 text-sm">
              No subdirectories
            </div>
          ) : (
            <ul>
              {dirs.map(item => (
                <li key={item.path}>
                  <button
                    onClick={() => navigate(item.path)}
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-gray-800/60 transition-colors group"
                  >
                    <Folder size={15} className="text-blue-400 shrink-0" />
                    <span className="text-sm text-gray-300 group-hover:text-white truncate flex-1">
                      {item.name}
                    </span>
                    <ChevronRight size={13} className="text-gray-600 group-hover:text-gray-400 shrink-0" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Footer — select current directory */}
        <div className="px-4 py-3 border-t border-gray-700 flex items-center justify-between gap-3">
          <span className="text-xs text-gray-500 font-mono truncate flex-1" title={currentPath}>
            {currentPath || '—'}
          </span>
          <div className="flex gap-2 shrink-0">
            <button
              onClick={onClose}
              className="px-3 py-1.5 rounded-lg text-xs text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => { onSelect(currentPath); onClose() }}
              disabled={!currentPath}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                         bg-violet-600 hover:bg-violet-500 text-white
                         disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Check size={13} />
              Select
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
