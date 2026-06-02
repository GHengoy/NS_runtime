import { useState } from 'react'
import { X, Layers } from 'lucide-react'
import { InspectionLine } from '../types'

interface Props {
  lines: InspectionLine[]
  onClose: () => void
  onApply: (productName: string) => Promise<void>
}

export default function SwitchProductAllModal({ lines, onClose, onApply }: Props) {
  // Collect unique product names and how many lines have each
  const productMap = new Map<string, number>()
  for (const line of lines) {
    if (line.config.products) {
      for (const name of Object.keys(line.config.products)) {
        productMap.set(name, (productMap.get(name) ?? 0) + 1)
      }
    }
  }

  const productList = [...productMap.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  const totalLines = lines.filter(l => l.config.enabled !== false).length

  const [selected, setSelected] = useState(productList[0]?.[0] ?? '')
  const [loading, setLoading] = useState(false)

  const affectedCount = productMap.get(selected) ?? 0

  const handleApply = async () => {
    if (!selected) return
    setLoading(true)
    try {
      await onApply(selected)
      onClose()
    } catch {
      setLoading(false)
    }
  }

  if (productList.length === 0) {
    return (
      <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
        <div className="bg-[#1e2130] border border-gray-700/50 rounded-xl p-6 w-full max-w-sm">
          <p className="text-gray-400 text-sm">No products configured yet.</p>
          <button onClick={onClose} className="mt-4 text-sm text-gray-500 hover:text-gray-300 transition-colors">Close</button>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-[#1e2130] border border-gray-700/50 rounded-xl w-full max-w-sm shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700/40">
          <div className="flex items-center gap-2.5">
            <Layers size={15} className="text-blue-400" />
            <span className="text-sm font-semibold text-white">Switch Product — All Lines</span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="p-4">
          <p className="text-xs text-gray-500 mb-3">
            Select a product to apply across all lines. Lines that don't have the product will be skipped.
          </p>
          <div className="space-y-1 max-h-56 overflow-y-auto pr-1">
            {productList.map(([name, count]) => (
              <button
                key={name}
                onClick={() => setSelected(name)}
                className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all flex items-center justify-between ${
                  selected === name
                    ? 'bg-blue-600/20 border border-blue-500/40 text-white'
                    : 'text-gray-400 border border-transparent hover:bg-gray-700/40 hover:text-gray-200'
                }`}
              >
                <span className="font-medium">{name}</span>
                <span className={`text-xs ${selected === name ? 'text-blue-300' : 'text-gray-600'}`}>
                  {count} / {totalLines} lines
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2.5 px-5 py-4 border-t border-gray-700/40">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm text-gray-400 border border-gray-600/50 hover:text-white hover:bg-gray-700/40 transition-all"
          >
            Cancel
          </button>
          <button
            onClick={handleApply}
            disabled={loading || !selected}
            className="px-4 py-2 rounded-lg text-sm font-semibold transition-all disabled:opacity-50 disabled:cursor-not-allowed
              bg-blue-600/20 text-blue-300 border border-blue-500/40 hover:bg-blue-600/30 hover:border-blue-500/60"
          >
            {loading ? 'Switching...' : `Apply to ${affectedCount} ${affectedCount === 1 ? 'line' : 'lines'}`}
          </button>
        </div>
      </div>
    </div>
  )
}
