import { useState, useEffect, useCallback } from 'react'
import { KeyRound, Lock, Eye, EyeOff, CheckCircle2, XCircle, Loader2, HardDrive, FolderOpen, Folder, ArrowLeft, X } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { changeAdminPassword, fetchStorageSettings, updateStorageSettings, browseFiles, type FileBrowseItem } from '../api'
import type { StorageSettings } from '../types'

// ── Folder Browser (inline, reusable) ──────────────────────────────
function FolderBrowser({ onSelect, onClose, initialPath }: {
  onSelect: (path: string) => void
  onClose: () => void
  initialPath?: string
}) {
  const [currentDir, setCurrentDir] = useState('')
  const [parentDir, setParentDir] = useState('')
  const [items, setItems] = useState<FileBrowseItem[]>([])
  const [loading, setLoading] = useState(true)

  const browse = useCallback(async (path: string) => {
    setLoading(true)
    try {
      const res = await browseFiles(path, '')
      setCurrentDir(res.current)
      setParentDir(res.parent)
      setItems(res.items.filter(i => i.is_dir))
    } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => {
    browse(initialPath || '')
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="mt-2 bg-gray-900/80 border border-gray-700 rounded-lg max-h-64 flex flex-col overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-700 bg-gray-900/50">
        <button type="button" onClick={() => browse(parentDir)}
          className="p-1 text-gray-400 hover:text-white rounded hover:bg-gray-700" title="Go up">
          <ArrowLeft size={14} />
        </button>
        <span className="text-[10px] text-gray-500 font-mono truncate flex-1" title={currentDir}>{currentDir}</span>
        <button type="button" onClick={() => onSelect(currentDir)}
          className="px-2 py-0.5 text-[10px] bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors">
          Select
        </button>
        <button type="button" onClick={onClose} className="p-1 text-gray-500 hover:text-gray-300">
          <X size={12} />
        </button>
      </div>
      <div className="overflow-y-auto flex-1">
        {loading ? (
          <div className="text-xs text-gray-500 py-4 text-center">Loading...</div>
        ) : items.length === 0 ? (
          <div className="text-xs text-gray-600 py-4 text-center">No subfolders</div>
        ) : (
          items.map(item => (
            <button key={item.path} type="button" onClick={() => browse(item.path)}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-300 hover:bg-gray-700/60 transition-colors">
              <Folder size={13} className="text-yellow-500/70 shrink-0" />
              <span className="truncate">{item.name}</span>
            </button>
          ))
        )}
      </div>
    </div>
  )
}

export default function Admin() {
  const { requireAdmin, isAdminAuthenticated } = useAuth()
  const [verifiedPassword, setVerifiedPassword] = useState('')

  // Password
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)

  // Storage
  const [storageSettings, setStorageSettings] = useState<StorageSettings | null>(null)
  const [saveRoot, setSaveRoot] = useState('./data')
  const [retentionDays, setRetentionDays] = useState(180)
  const [storageSaving, setStorageSaving] = useState(false)
  const [storageResult, setStorageResult] = useState<{ ok: boolean; msg: string } | null>(null)
  const [showBrowser, setShowBrowser] = useState(false)

  // Load storage settings
  useEffect(() => {
    if (isAdminAuthenticated) {
      fetchStorageSettings().then(s => {
        setStorageSettings(s)
        setSaveRoot(s.save_root || './data')
        setRetentionDays(s.local_retention_days)
      }).catch(() => {})
    }
  }, [isAdminAuthenticated])

  const canSubmit =
    newPassword.length > 0 &&
    confirmPassword.length > 0 &&
    newPassword === confirmPassword &&
    !saving

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return

    if (newPassword !== confirmPassword) {
      setResult({ ok: false, msg: 'New passwords do not match' })
      return
    }

    setSaving(true)
    setResult(null)
    try {
      await changeAdminPassword(verifiedPassword, newPassword)
      setResult({ ok: true, msg: 'Password changed successfully' })
      setVerifiedPassword(newPassword)
      setNewPassword('')
      setConfirmPassword('')
    } catch (err: any) {
      const msg = err?.message || 'Failed to change password'
      setResult({ ok: false, msg: msg.includes('403') ? 'Current password is incorrect' : msg })
    } finally {
      setSaving(false)
    }
  }

  async function handleStorageSave() {
    if (!storageSettings) return
    setStorageSaving(true)
    setStorageResult(null)
    try {
      await updateStorageSettings({
        ...storageSettings,
        save_root: saveRoot,
        local_retention_days: retentionDays,
      })
      setStorageResult({ ok: true, msg: 'Storage settings saved. Restart workers to apply.' })
    } catch (err: any) {
      setStorageResult({ ok: false, msg: err?.message || 'Failed to save' })
    } finally {
      setStorageSaving(false)
    }
  }

  if (!isAdminAuthenticated) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4">
        <div className="w-16 h-16 rounded-full bg-gray-800 flex items-center justify-center">
          <Lock size={28} className="text-gray-500" />
        </div>
        <h2 className="text-base font-medium text-white">Admin Settings</h2>
        <p className="text-sm text-gray-500">Admin password required to access this page</p>
        <button
          onClick={() => requireAdmin((pw) => { setVerifiedPassword(pw) })}
          className="px-5 py-2 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors"
        >
          Unlock
        </button>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-700/40 shrink-0">
        <div className="w-9 h-9 rounded-lg bg-gray-800 flex items-center justify-center">
          <KeyRound size={18} className="text-gray-400" />
        </div>
        <div className="flex-1">
          <h1 className="text-base font-semibold text-white">Admin Settings</h1>
          <p className="text-xs text-gray-500">Manage storage and security settings</p>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-md space-y-6">

          {/* Data Storage Card */}
          <div className="rounded-xl border border-gray-700/50 bg-gray-800/30 p-6">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-10 h-10 rounded-lg bg-green-500/10 flex items-center justify-center">
                <HardDrive size={20} className="text-green-400" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-white">Data Storage</h2>
                <p className="text-xs text-gray-500">Global save location for all workers</p>
              </div>
            </div>

            <div className="space-y-4">
              {/* Save Root */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Save Root Path</label>
                <div className="flex gap-1.5">
                  <input
                    type="text"
                    value={saveRoot}
                    onChange={e => { setSaveRoot(e.target.value); setStorageResult(null) }}
                    placeholder="e.g. ./data or /mnt/nas/inspection"
                    className="flex-1 px-3 py-2 rounded-lg bg-gray-900/60 border border-gray-700/50 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                  />
                  <button
                    type="button"
                    onClick={() => setShowBrowser(!showBrowser)}
                    className={`px-2.5 rounded-lg border text-xs transition-colors ${
                      showBrowser ? 'bg-blue-600 border-blue-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white hover:border-gray-500'
                    }`}
                  >
                    <FolderOpen size={14} />
                  </button>
                </div>
                {showBrowser && (
                  <FolderBrowser
                    initialPath={saveRoot !== './data' ? saveRoot : undefined}
                    onSelect={(path) => { setSaveRoot(path); setShowBrowser(false); setStorageResult(null) }}
                    onClose={() => setShowBrowser(false)}
                  />
                )}
                <p className="text-[10px] text-gray-600 mt-1">
                  All defect images are saved here. Structure: save_root/defect/worker-name/class/date/
                </p>
              </div>

              {/* Retention Days */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Retention Days</label>
                <input
                  type="number"
                  min={0}
                  step={1}
                  value={retentionDays}
                  onChange={e => { setRetentionDays(+e.target.value); setStorageResult(null) }}
                  className="w-full px-3 py-2 rounded-lg bg-gray-900/60 border border-gray-700/50 text-sm text-white focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                />
                <p className="text-[10px] text-gray-600 mt-1">
                  Auto-delete data older than this. 0 = keep forever.
                </p>
              </div>

              {/* Result Message */}
              {storageResult && (
                <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm ${
                  storageResult.ok
                    ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                    : 'bg-red-500/10 text-red-400 border border-red-500/20'
                }`}>
                  {storageResult.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
                  {storageResult.msg}
                </div>
              )}

              {/* Save Button */}
              <button
                type="button"
                onClick={handleStorageSave}
                disabled={storageSaving}
                className="w-full py-2.5 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {storageSaving ? (
                  <>
                    <Loader2 size={15} className="animate-spin" />
                    Saving...
                  </>
                ) : (
                  'Save Storage Settings'
                )}
              </button>
            </div>
          </div>

          {/* Change Password Card */}
          <div className="rounded-xl border border-gray-700/50 bg-gray-800/30 p-6">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center">
                <Lock size={20} className="text-blue-400" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-white">Change Password</h2>
                <p className="text-xs text-gray-500">Update the admin password for this system</p>
              </div>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
              {/* New Password */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">New Password</label>
                <div className="relative">
                  <input
                    type={showNew ? 'text' : 'password'}
                    value={newPassword}
                    onChange={e => { setNewPassword(e.target.value); setResult(null) }}
                    className="w-full px-3 py-2 pr-10 rounded-lg bg-gray-900/60 border border-gray-700/50 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                    placeholder="Enter new password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowNew(p => !p)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
                  >
                    {showNew ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>

              {/* Confirm Password */}
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Confirm New Password</label>
                <div className="relative">
                  <input
                    type={showConfirm ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={e => { setConfirmPassword(e.target.value); setResult(null) }}
                    className={`w-full px-3 py-2 pr-10 rounded-lg bg-gray-900/60 border text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-1 ${
                      confirmPassword && newPassword !== confirmPassword
                        ? 'border-red-500/50 focus:border-red-500/50 focus:ring-red-500/20'
                        : 'border-gray-700/50 focus:border-blue-500/50 focus:ring-blue-500/20'
                    }`}
                    placeholder="Re-enter new password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirm(p => !p)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
                  >
                    {showConfirm ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                {confirmPassword && newPassword !== confirmPassword && (
                  <p className="text-xs text-red-400 mt-1">Passwords do not match</p>
                )}
              </div>

              {/* Result Message */}
              {result && (
                <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm ${
                  result.ok
                    ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                    : 'bg-red-500/10 text-red-400 border border-red-500/20'
                }`}>
                  {result.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
                  {result.msg}
                </div>
              )}

              {/* Submit */}
              <button
                type="submit"
                disabled={!canSubmit}
                className="w-full py-2.5 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-blue-600"
              >
                {saving ? (
                  <>
                    <Loader2 size={15} className="animate-spin" />
                    Changing...
                  </>
                ) : (
                  'Change Password'
                )}
              </button>
            </form>
          </div>

        </div>
      </div>
    </div>
  )
}
