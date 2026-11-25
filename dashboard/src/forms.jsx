import { cloneElement, useEffect, useId, useRef, useState } from 'react'
import { AlertCircle, Archive, X } from 'lucide-react'
import { ingredientOptions, inventoryPath, request, useResource } from './api'
import { ErrorState, Loading } from './ui'

function Field({ label, hint, children }) {
  const fieldId = useId()
  const hintId = hint ? fieldId + '-hint' : undefined
  return (
    <div className="form-field">
      <label htmlFor={fieldId}>{label}</label>
      {cloneElement(children, { id: fieldId, 'aria-describedby': hintId })}
      {hint && <small id={hintId} className="form-hint">{hint}</small>}
    </div>
  )
}

const numericPattern = '[0-9]{1,9}(\\.[0-9]{1,3})?'

function QuantityField({ label, value, onChange, required }) {
  return (
    <Field label={label} hint="Up to three decimal places.">
      <input value={value} onChange={event => onChange(event.target.value)} inputMode="decimal"
        pattern={numericPattern} maxLength={13} required={required} />
    </Field>
  )
}

function IngredientForm({ draft, setField }) {
  return (
    <>
      <Field label="Ingredient name"><input required maxLength={150} value={draft.name} onChange={event => setField('name', event.target.value)} /></Field>
      <div className="form-grid">
        <Field label="Category"><input required maxLength={100} value={draft.category} onChange={event => setField('category', event.target.value)} /></Field>
        <Field label="Stock unit"><input required maxLength={40} placeholder="e.g. kg" value={draft.unit} onChange={event => setField('unit', event.target.value)} /></Field>
      </div>
      <QuantityField label="Minimum stock" value={draft.minimum_stock} onChange={value => setField('minimum_stock', value)} required />
    </>
  )
}

function IngredientSelect({ value, onChange, onCreate }) {
  const resource = useResource(ingredientOptions)
  if (resource.loading) return <Loading label="Loading ingredient options" />
  if (resource.error) return <ErrorState error={resource.error} retry={resource.retry} />
  if (!resource.data.length) return (
    <div className="inline-notice">
      <p>Add an ingredient before creating a stock lot.</p>
      <button type="button" className="button secondary" onClick={onCreate}>Add ingredient</button>
    </div>
  )
  return (
    <Field label="Ingredient">
      <select required value={value} onChange={event => onChange(event.target.value)}>
        <option value="">Choose an ingredient</option>
        {resource.data.map(item => <option key={item.id} value={item.id}>{item.name} · {item.category} ({item.unit})</option>)}
      </select>
    </Field>
  )
}

function LotForm({ item, draft, setField, onCreateIngredient }) {
  return (
    <>
      {item ? <p className="form-context">{item.ingredient.name} <span>· Lot #{item.id}</span></p> :
        <IngredientSelect value={draft.ingredient_id} onChange={value => setField('ingredient_id', value)} onCreate={onCreateIngredient} />}
      {item?.source === 'csv_import' && <p className="form-context">Imported demo lot</p>}
      {!item?.expiry_date && item?.source_expiry_text && <p className="inline-notice">The source CSV listed {item.source_expiry_text}. Enter a verified expiry date when available.</p>}
      <div className="form-grid">
        <QuantityField label={item ? 'Quantity (' + item.ingredient.unit + ')' : 'Quantity'}
          value={draft.quantity} onChange={value => setField('quantity', value)} required />
        <Field label="Expiry date" hint="Leave blank if the date is unknown.">
          <input type="date" max="9999-12-31" value={draft.expiry_date} onChange={event => setField('expiry_date', event.target.value)} />
        </Field>
      </div>
      <div className="form-grid">
        <QuantityField label="Cost per unit (optional)" value={draft.cost_per_unit} onChange={value => setField('cost_per_unit', value)} />
        <Field label="Days after restock (optional)">
          <input type="number" min="0" max="100000" step="1" value={draft.days_after_restock}
            onChange={event => setField('days_after_restock', event.target.value)} />
        </Field>
      </div>
    </>
  )
}

function draftFor(ingredient, item) {
  if (ingredient) return {
    name: item?.name ?? '', category: item?.category ?? '', unit: item?.unit ?? '',
    minimum_stock: item?.minimum_stock ?? '0',
  }
  return {
    ingredient_id: item ? String(item.ingredient_id) : '',
    quantity: item?.quantity ?? '', expiry_date: item?.expiry_date ?? '', cost_per_unit: item?.cost_per_unit ?? '',
    days_after_restock: item?.days_after_restock == null ? '' : String(item.days_after_restock),
  }
}

function bodyFor(draft, initial, editing) {
  const keys = Object.keys(draft).filter(key => !editing || draft[key] !== initial[key])
  return Object.fromEntries(keys.map(key => {
    const value = draft[key].trim()
    if (key === 'days_after_restock' || key === 'ingredient_id') return [key, value === '' ? null : Number(value)]
    if (key === 'expiry_date' || key === 'cost_per_unit') return [key, value || null]
    return [key, value]
  }))
}

export function InventoryDialog({ spec, onClose, onSave, openDialog }) {
  const { type, item } = spec
  const archive = type === 'archiveLot' || type === 'archiveIngredient'
  const ingredient = type === 'ingredient' || type === 'archiveIngredient'
  const noun = ingredient ? 'ingredient' : 'stock lot'
  const action = archive ? 'Archive' : item ? 'Edit' : 'Add'
  const title = action + ' ' + noun
  const endpoint = inventoryPath + (ingredient ? '/ingredients' : '') + (item ? '/' + item.id : '')
  const [draft, setDraft] = useState(() => draftFor(ingredient, item))
  const initial = useRef(draft)
  const saving = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dialogRef = useRef(null)
  const titleId = useId()

  useEffect(() => {
    const dialog = dialogRef.current
    const opener = document.activeElement
    dialog.showModal()
    return () => {
      if (dialog.open) dialog.close()
      if (opener?.isConnected) opener.focus()
    }
  }, [])

  function setField(field, value) {
    setDraft(previous => ({ ...previous, [field]: value }))
  }

  async function submit(event) {
    event.preventDefault()
    if (saving.current) return
    if (!archive && !ingredient && !item && !draft.ingredient_id) {
      setError('Choose an ingredient before saving this stock lot.')
      return
    }
    const body = archive ? undefined : bodyFor(draft, initial.current, Boolean(item))
    if (item && !archive && Object.keys(body).length === 0) {
      onClose()
      return
    }
    saving.current = true
    setBusy(true)
    setError('')
    try {
      await request(endpoint, { method: archive ? 'DELETE' : item ? 'PATCH' : 'POST', body })
      onSave((ingredient ? 'Ingredient' : 'Stock lot') + (archive ? ' archived.' : item ? ' updated.' : ' added.'))
    } catch (error) {
      setError(error.message)
      setBusy(false)
      saving.current = false
    }
  }

  return (
    <dialog ref={dialogRef} className="inventory-dialog" aria-labelledby={titleId}
      onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
      <form onSubmit={submit} aria-busy={busy}>
        <header className="dialog-header">
          <div>
            <span className={'dialog-icon ' + (archive ? 'tone-red' : 'tone-green')}><Archive size={20} /></span>
            <h2 id={titleId}>{title}</h2>
          </div>
          <button type="button" className="icon-button quiet" aria-label="Close dialog" onClick={onClose} disabled={busy}><X size={20} /></button>
        </header>
        <fieldset className="dialog-fields" disabled={busy}>
          {archive ? (
            <p className="archive-description">
              Archive <strong>{ingredient ? item.name : item.ingredient.name + ' lot #' + item.id}</strong>?
              {' '}{ingredient ? 'Its active lots must be archived first.' : 'The remaining balance will be removed and the stock history will be retained.'}
            </p>
          ) : ingredient ? <IngredientForm draft={draft} setField={setField} /> :
            <LotForm item={item} draft={draft} setField={setField} onCreateIngredient={() => openDialog({ type: 'ingredient' })} />}
          {error && <p className="form-error" role="alert"><AlertCircle size={17} />{error}</p>}
        </fieldset>
        <footer className="dialog-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className={'button ' + (archive ? 'danger' : 'primary')}
            disabled={busy || (!archive && !ingredient && !item && !draft.ingredient_id)}>
            {busy ? 'Saving…' : archive ? 'Archive ' + noun : 'Save ' + noun}
          </button>
        </footer>
      </form>
    </dialog>
  )
}
