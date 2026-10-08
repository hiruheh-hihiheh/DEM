import React, { useRef, useState } from 'react'
import { uploadImport } from '../services/api'

interface ImportDropzoneProps {
  onImported: (jobId: string) => void
}

/**
 * Drag-and-drop / browse ZIP importer. The ZIP must contain PartVTK
 * frames (particles/PartFluid_*.vtk) — raw .bi4 output is not parsed.
 */
export const ImportDropzone: React.FC<ImportDropzoneProps> = ({
  onImported,
}) => {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleFile = async (file: File | undefined | null) => {
    if (!file) return
    setError(null)

    if (!file.name.toLowerCase().endsWith('.zip')) {
      setError(
        'Please choose a .zip package. Individual VTK files are not supported — zip the run output folder instead.',
      )
      return
    }

    setBusy(true)
    try {
      const res = await uploadImport(file)
      onImported(res.job_id)
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response
          ?.data?.detail
      setError(detail ?? 'Upload failed — is the backend running?')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div
        className={`dropzone${over ? ' dropzone--over' : ''}`}
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            inputRef.current?.click()
          }
        }}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          void handleFile(e.dataTransfer.files?.[0])
        }}
      >
        <div className="dropzone__icon" aria-hidden="true">
          {busy ? '⏳' : '📦'}
        </div>
        <div className="dropzone__title">
          {busy ? 'Importing…' : 'Drop a simulation ZIP here, or click to browse'}
        </div>
        <div className="dropzone__hint">
          ZIP must contain PartVTK frames (particles/PartFluid_*.vtk).
          Raw .bi4 output is not supported.
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".zip"
          hidden
          onChange={(e) => {
            void handleFile(e.target.files?.[0])
            e.target.value = ''
          }}
        />
      </div>

      {error && <div className="error-banner">{error}</div>}
    </div>
  )
}
