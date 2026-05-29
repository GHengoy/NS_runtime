import { useEffect, useState } from 'react'
import { X, Loader2, CheckCircle2, XCircle, Upload, Play } from 'lucide-react'
import { fetchStorageSettings, updateStorageSettings, testStorageConnection, triggerCleanupNow } from '../api'
import type { StorageType } from '../types'

const S3_REGIONS = [
  'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2',
  'ap-northeast-1', 'ap-northeast-2', 'ap-southeast-1', 'ap-southeast-2',
  'eu-west-1', 'eu-west-2', 'eu-central-1', 'sa-east-1',
]

interface Props {
  open: boolean
  onClose: () => void
  onSaved?: () => void
}

export default function S3ConfigModal({ open, onClose, onSaved }: Props) {
  const [localRetentionDays, setLocalRetentionDays] = useState(180)
  const [saveRoot, setSaveRoot] = useState('./data')
  const [storageType, setStorageType] = useState<StorageType>('local')
  const [s3Bucket, setS3Bucket] = useState('')
  const [s3Region, setS3Region] = useState('us-east-1')
  const [s3AccessKey, setS3AccessKey] = useState('')
  const [s3SecretKey, setS3SecretKey] = useState('')
  const [s3Prefix, setS3Prefix] = useState('')
  const [s3RetentionDays, setS3RetentionDays] = useState(365)
  const [s3CleanupInterval, setS3CleanupInterval] = useState(6)
  const [showSecret, setShowSecret] = useState(false)
  const [syncStats, setSyncStats] = useState<{ queue_size: number; uploaded: number; errors: number } | null>(null)

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [cleaning, setCleaning] = useState(false)
  const [cleanResult, setCleanResult] = useState<{ ok: boolean; text: string } | null>(null)
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    fetchStorageSettings()
      .then(s => {
        setSaveRoot(s.save_root || './data')
        setLocalRetentionDays(s.local_retention_days ?? 180)
        setStorageType(s.storage_type)
        setS3Bucket(s.s3_bucket)
        setS3Region(s.s3_region)
        setS3AccessKey(s.s3_access_key)
        setS3SecretKey(s.s3_secret_key)
        setS3Prefix(s.s3_prefix)
        setS3RetentionDays(s.s3_retention_days ?? 365)
        setS3CleanupInterval(s.s3_cleanup_interval_hours ?? 6)
        setSyncStats(s.s3_sync_stats ?? null)
        setDirty(false)
        setSaveMsg(null)
        setTestResult(null)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [open])

  if (!open) return null

  const markDirty = () => { setDirty(true); setSaveMsg(null); setTestResult(null); setCleanResult(null) }

  const handleCleanup = async () => {
    setCleaning(true)
    setCleanResult(null)
    try {
      await triggerCleanupNow()
      setCleanResult({ ok: true, text: 'Cleanup completed' })
    } catch (e: any) {
      setCleanResult({ ok: false, text: e.message || 'Cleanup failed' })
    } finally {
      setCleaning(false)
    }
  }

  const currentPayload = () => ({
    save_root: saveRoot,
    local_retention_days: localRetentionDays,
    storage_type: storageType,
    s3_bucket: s3Bucket,
    s3_region: s3Region,
    s3_access_key: s3AccessKey,
    s3_secret_key: s3SecretKey,
    s3_prefix: s3Prefix,
    s3_retention_days: s3RetentionDays,
    s3_cleanup_interval_hours: s3CleanupInterval,
  })

  const handleSave = async () => {
    setSaving(true)
    setSaveMsg(null)
    try {
      await updateStorageSettings(currentPayload())
      setDirty(false)
      setSaveMsg({ ok: true, text: 'Settings saved successfully' })
      const fresh = await fetchStorageSettings()
      setS3SecretKey(fresh.s3_secret_key)
      setSyncStats(fresh.s3_sync_stats ?? null)
      onSaved?.()
    } catch (e: any) {
      setSaveMsg({ ok: false, text: e.message || 'Failed to save' })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await testStorageConnection(currentPayload())
      setTestResult(result)
    } catch (e: any) {
      setTestResult({ success: false, message: e.message || 'Test failed' })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
         onClick={onClose}>
      <div className="bg-[#2c313a] border border-gray-700/50 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700/40">
          <h2 className="text-sm font-medium text-white">Storage Configuration</h2>
          <button onClick={onClose} className="p-1 text-gray-500 hover:text-gray-300">
            <X size={16} />
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="animate-spin text-gray-500" size={24} />
          </div>
        ) : (
          <div className="px-6 py-5 space-y-5">
            {/* Storage Type */}
            <div>
              <span className="text-xs font-medium text-gray-500 uppercase tracking-wider block mb-3">Storage Type</span>
              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => { setStorageType('local'); markDirty() }}
                  className={`flex flex-col items-center gap-1 p-3 rounded-lg border-2 transition-all text-center ${
                    storageType === 'local' ? 'border-blue-500 bg-blue-500/10' : 'border-gray-700 bg-gray-800/60 hover:border-gray-600'
                  }`}
                >
                  <span className={`text-sm font-medium ${storageType === 'local' ? 'text-blue-400' : 'text-gray-300'}`}>Local Only</span>
                  <span className="text-xs text-gray-500">Save to local disk</span>
                </button>
                <button
                  onClick={() => { setStorageType('s3'); markDirty() }}
                  className={`flex flex-col items-center gap-1 p-3 rounded-lg border-2 transition-all text-center ${
                    storageType === 's3' ? 'border-blue-500 bg-blue-500/10' : 'border-gray-700 bg-gray-800/60 hover:border-gray-600'
                  }`}
                >
                  <span className={`text-sm font-medium ${storageType === 's3' ? 'text-blue-400' : 'text-gray-300'}`}>Local + S3</span>
                  <span className="text-xs text-gray-500">Sync to AWS S3</span>
                </button>
              </div>
            </div>

            <div className="border-t border-gray-700/40" />

            {/* Cleanup Cycle */}
            <div>
              <span className="text-xs font-medium text-gray-500 uppercase tracking-wider block mb-3">Cleanup Cycle</span>
              <div className="flex items-end gap-3">
                <label className="block flex-1">
                  <span className="text-xs text-gray-400 mb-1 block">Interval (hours)</span>
                  <input type="number" min={1} value={s3CleanupInterval}
                         onChange={e => { setS3CleanupInterval(Math.max(1, parseInt(e.target.value) || 1)); markDirty() }}
                         className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                </label>
                <button onClick={handleCleanup} disabled={cleaning}
                        className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-gray-700 hover:bg-gray-600 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap">
                  {cleaning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  Run Now
                </button>
              </div>
              <span className="text-[10px] text-gray-600 mt-1 block">Applies to local & S3 cleanup</span>
              {cleanResult && (
                <div className={`flex items-center gap-1.5 text-xs mt-1 ${cleanResult.ok ? 'text-green-400' : 'text-red-400'}`}>
                  {cleanResult.ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                  {cleanResult.text}
                </div>
              )}
            </div>

            <div className="border-t border-gray-700/40" />

            {/* Local Data Retention */}
            <div>
              <span className="text-xs font-medium text-gray-500 uppercase tracking-wider block mb-3">Local Data Retention</span>
              <label className="block">
                <span className="text-xs text-gray-400 mb-1 block">Retention Period (days)</span>
                <input type="number" min={0} value={localRetentionDays}
                       onChange={e => { setLocalRetentionDays(Math.max(0, parseInt(e.target.value) || 0)); markDirty() }}
                       className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                <span className="text-[10px] text-gray-600 mt-0.5 block">0 = unlimited · default 180 days</span>
              </label>
            </div>

            {/* S3 Fields */}
            {storageType === 's3' && (
              <>
                <div className="border-t border-gray-700/40" />
                <div className="space-y-3">
                  <span className="text-xs font-medium text-gray-500 uppercase tracking-wider block">S3 Connection</span>
                  <label className="block">
                    <span className="text-xs text-gray-400 mb-1 block">Bucket Name<span className="text-red-400 ml-0.5">*</span></span>
                    <input value={s3Bucket} onChange={e => { setS3Bucket(e.target.value); markDirty() }}
                           placeholder="my-inspection-bucket"
                           className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500" />
                  </label>
                  <label className="block">
                    <span className="text-xs text-gray-400 mb-1 block">Region</span>
                    <select value={s3Region} onChange={e => { setS3Region(e.target.value); markDirty() }}
                            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
                      {S3_REGIONS.map(r => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </label>
                  <label className="block">
                    <span className="text-xs text-gray-400 mb-1 block">Access Key ID</span>
                    <input value={s3AccessKey} onChange={e => { setS3AccessKey(e.target.value); markDirty() }}
                           placeholder="AKIAIOSFODNN7EXAMPLE"
                           className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono" />
                  </label>
                  <label className="block">
                    <span className="text-xs text-gray-400 mb-1 block">Secret Access Key</span>
                    <input value={s3SecretKey}
                           onChange={e => { setS3SecretKey(e.target.value); markDirty() }}
                           placeholder="wJalrXUtnFEMI/K7MDENG/bPxRfi..."
                           className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono" />
                  </label>

                  {/* S3 Data Retention */}
                  <div className="border-t border-gray-700/40 pt-3">
                    <span className="text-xs font-medium text-gray-500 uppercase tracking-wider block mb-3">S3 Data Retention</span>
                    <label className="block">
                      <span className="text-xs text-gray-400 mb-1 block">Retention Period (days)</span>
                      <input type="number" min={0} value={s3RetentionDays}
                             onChange={e => { setS3RetentionDays(Math.max(0, parseInt(e.target.value) || 0)); markDirty() }}
                             className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                      <span className="text-[10px] text-gray-600 mt-0.5 block">0 = unlimited</span>
                    </label>
                  </div>

                  {/* Test Connection */}
                  <button onClick={handleTest} disabled={testing || !s3Bucket}
                          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-gray-700 hover:bg-gray-600 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                    {testing ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                    Test Connection
                  </button>
                  {testResult && (
                    <div className={`flex items-center gap-2 text-xs ${testResult.success ? 'text-green-400' : 'text-red-400'}`}>
                      {testResult.success ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                      {testResult.message}
                    </div>
                  )}

                  {/* Sync Stats */}
                  {syncStats && (
                    <>
                      <div className="border-t border-gray-700/40" />
                      <div>
                        <span className="text-xs font-medium text-gray-500 uppercase tracking-wider block mb-2">Sync Status</span>
                        <div className="grid grid-cols-3 gap-3">
                          <div className="bg-gray-800/80 rounded-lg p-2 text-center">
                            <p className="text-base font-semibold text-white">{syncStats.queue_size}</p>
                            <p className="text-xs text-gray-500">Pending</p>
                          </div>
                          <div className="bg-gray-800/80 rounded-lg p-2 text-center">
                            <p className="text-base font-semibold text-green-400">{syncStats.uploaded}</p>
                            <p className="text-xs text-gray-500">Uploaded</p>
                          </div>
                          <div className="bg-gray-800/80 rounded-lg p-2 text-center">
                            <p className={`text-base font-semibold ${syncStats.errors > 0 ? 'text-red-400' : 'text-white'}`}>{syncStats.errors}</p>
                            <p className="text-xs text-gray-500">Errors</p>
                          </div>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-700/40 flex items-center gap-3">
          <button onClick={handleSave} disabled={saving || !dirty}
                  className="flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            {saving && <Loader2 size={14} className="animate-spin" />}
            Save
          </button>
          <button onClick={onClose}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-gray-400 hover:text-white hover:bg-gray-700 transition-colors">
            Cancel
          </button>
          {saveMsg && (
            <span className={`text-xs ${saveMsg.ok ? 'text-green-400' : 'text-red-400'}`}>
              {saveMsg.text}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
