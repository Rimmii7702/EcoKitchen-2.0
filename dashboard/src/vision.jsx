import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Clock3, ShieldCheck, UploadCloud } from 'lucide-react'
import { apiBase, request } from './api'
import { formatDate, formatTime } from './format'
import { Badge, DataTable, EmptyState, Loading, PageHeading, PanelHeading } from './ui'
import './vision.css'

const imageExtensions = ['jpg', 'jpeg', 'png', 'bmp']
const mode = {
  extensions: imageExtensions, size: 10, action: 'Run spoilage check',
}

export function mediaUrl(path) {
  return typeof path === 'string' && path.startsWith('/api/vision/media/') ? apiBase + path : null
}

function percentage(value, multiplier = 1) {
  if (typeof value !== 'number' && (typeof value !== 'string' || !value.trim())) return null
  const amount = Number(value) * multiplier
  return Number.isFinite(amount) && amount >= 0 && amount <= 100
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(amount) + '%'
    : null
}

export function usePreview(file) {
  const [preview, setPreview] = useState(null)
  useEffect(() => {
    if (!file) return undefined
    const url = URL.createObjectURL(file)
    setPreview({ file, url })
    return () => URL.revokeObjectURL(url)
  }, [file])
  return preview?.file === file ? preview.url : null
}

export function MediaPreview({ src, alt, stored = false }) {
  const [unavailable, setUnavailable] = useState(false)
  return unavailable
    ? <p className="vision-media-error">This preview could not be displayed.</p>
    : <img className="vision-media" src={src} alt={alt}
      crossOrigin={stored ? 'use-credentials' : undefined} onError={() => setUnavailable(true)} />
}

function SpoilageTable({ items }) {
  const hasScores = items.some(item => percentage(item.confidence) !== null)
  const hasActions = items.some(item => item.action)
  const columns = ['Ingredient', 'Assessment']
  if (hasScores) columns.push('Model confidence')
  if (hasActions) columns.push('Suggested action')
  return (
    <DataTable label="Spoilage assessment" columns={columns}>
      {items.map((item, index) => <tr key={index}>
        <td><strong>{item.item}</strong></td>
        <td>{item.status && <Badge tone={item.status === 'Rotten' ? 'red' : item.status === 'Fresh' ? 'green' : 'amber'}>{item.status}</Badge>}</td>
        {hasScores && <td>{percentage(item.confidence) ?? '—'}</td>}
        {hasActions && <td>{item.action ?? '—'}</td>}
      </tr>)}
    </DataTable>
  )
}

function SpoilageHistory({ checks }) {
  return (
    <section className="panel vision-history">
      <PanelHeading title="Recent spoilage checks" subtitle="Checks made while this page is open." />
      {checks.length ? <div className="vision-checks">
        {checks.map(check => (
          <details key={check.id}>
            <summary><strong>{check.filename}</strong>
              <time dateTime={check.checkedAt}>{formatDate(check.checkedAt)} · {formatTime(check.checkedAt)}</time>
            </summary>
            <SpoilageTable items={check.items} />
          </details>
        ))}
      </div> : <EmptyState title="No spoilage checks yet" icon={Clock3} />}
    </section>
  )
}

function UploadWorkflow({ onCheck }) {
  const [file, setFile] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const input = useRef(null)
  const activeRequest = useRef(null)
  const preview = usePreview(file)
  const accept = mode.extensions.map(extension => '.' + extension).join(',')

  useEffect(() => () => activeRequest.current?.abort(), [])

  function choose(selected) {
    if (!selected || activeRequest.current) return
    setDragging(false)
    setResult(null)
    setFile(null)
    setError(null)
    const extension = selected.name.split('.').at(-1)?.toLowerCase()
    if (!selected.name.includes('.') || !mode.extensions.includes(extension)) {
      setError({ message: 'Choose a file in one of the supported formats.' })
    } else if (!selected.size || selected.size > mode.size * 1024 ** 2) {
      setError({ message: selected.size ? 'The file exceeds the ' + mode.size + ' MiB limit.' : 'The selected file is empty.' })
    } else {
      setFile(selected)
    }
  }

  function clear() {
    if (input.current) input.current.value = ''
    setFile(null)
    setResult(null)
    setError(null)
  }

  async function submit(event) {
    event.preventDefault()
    if (!file || activeRequest.current) return
    const controller = new AbortController()
    activeRequest.current = controller
    setBusy(true)
    setError(null)
    setResult(null)
    const payload = new FormData()
    payload.append('image', file)
    try {
      const data = await request('/api/vision/spoilage', {
        method: 'POST', body: payload, signal: controller.signal, timeoutMs: 120000, service: 'vision',
      })
      if (controller.signal.aborted) return
      const entries = data.analysis
      if (!Array.isArray(entries)) throw new Error('The vision API returned an unexpected result.')
      setResult(data)
      if (entries.length) onCheck({
        filename: file.name, checkedAt: new Date().toISOString(), items: data.analysis,
      })
    } catch (error) {
      if (!controller.signal.aborted) setError(error)
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false)
        activeRequest.current = null
      }
    }
  }

  return (
    <div className="vision-workspace">
      <section className="panel vision-upload-panel">
        <PanelHeading title="Upload an image" subtitle="Send a food photo to the configured Gemini service." />
        <form className="vision-form" onSubmit={submit}>
          <label className={'vision-drop ' + (dragging ? 'vision-dragging' : '')}
            onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={event => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files[0]) }}>
            <input ref={input} type="file" className="sr-only" accept={accept} disabled={busy}
              onChange={event => choose(event.target.files[0])} />
            <span className="vision-drop-icon"><UploadCloud size={23} /></span>
            <strong>Choose an image</strong>
            <span>{mode.extensions.join(', ').toUpperCase()} · Up to {mode.size} MiB</span>
          </label>
          {file && <div className="vision-file">
            <span><strong>{file.name}</strong><small>{(file.size / 1024 ** 2).toFixed(2)} MiB</small></span>
            <button type="button" className="button quiet" disabled={busy} onClick={clear}>Remove</button>
          </div>}
          {preview && <figure className="vision-local-preview">
            <figcaption>Image preview</figcaption>
            <MediaPreview key={preview} src={preview} alt="Selected image preview" />
          </figure>}
          <button className="button primary vision-submit" type="submit" disabled={!file || busy}>
            <ShieldCheck size={17} />{busy ? 'Analyzing…' : mode.action}
          </button>
        </form>
      </section>
      <section className="panel vision-results-panel" aria-busy={busy}>
        <PanelHeading title="Gemini spoilage assessment" />
        {error ? <div className="error-state" role="alert">
          <AlertCircle size={20} />
          <div><strong>{error.status === 503 ? 'Analysis unavailable' : 'Analysis could not complete'}</strong><p>{error.message}</p></div>
        </div> : busy ? <Loading label="Uploading and analyzing image" /> :
          !result ? <EmptyState title="Ready for an upload" description="Choose a file to see the model results here." icon={ShieldCheck} /> :
                            result.analysis.length ? <SpoilageTable items={result.analysis} /> :
                  <EmptyState title="No spoilage assessment returned" icon={ShieldCheck} />}
      </section>
    </div>
  )
}

export function VisionPage({ canManage }) {
  const [checks, setChecks] = useState([])
  const checkNumber = useRef(0)
  if (!canManage) return <PageHeading title="Spoilage checks" description="Vision uploads require an administrator." />
  return (
    <>
      <PageHeading title="Spoilage checks" description="Image and video object detection are unavailable in this edition. Gemini spoilage checks require separate credentials." />
      <UploadWorkflow onCheck={check => {
        const id = ++checkNumber.current
        setChecks(previous => [{ ...check, id }, ...previous].slice(0, 8))
      }} />
      <SpoilageHistory checks={checks} />
    </>
  )
}
