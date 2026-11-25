import {
  AlertCircle, ArrowDownLeft, ArrowUpRight, Box, ChevronLeft, ChevronRight, CircleHelp,
  ClipboardList, Clock3, PackageOpen, Pencil, Plus, Search, Trash2,
} from 'lucide-react'
import { changeLabel, eventTypes, formatDate, formatTime, lotStatus, quantity } from './format'

export function PageHeading({ title, description, children }) {
  return (
    <div className="page-heading">
      <div><h1>{title}</h1>{description && <p>{description}</p>}</div>
      {children && <div className="heading-actions">{children}</div>}
    </div>
  )
}

export function AddButton({ children, onClick }) {
  return <button type="button" className="button primary" onClick={onClick}><Plus size={17} />{children}</button>
}

export function Badge({ tone = 'neutral', children }) {
  return <span className={'badge tone-' + tone}>{children}</span>
}

export function EmptyState({ title, description, icon: Icon = PackageOpen }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon size={24} /></span>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
    </div>
  )
}

export function ErrorState({ error, retry }) {
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={22} />
      <div><strong>Could not load inventory</strong><p>{error}</p></div>
      <button type="button" className="button secondary" onClick={retry}>Try again</button>
    </div>
  )
}

export function Loading({ label = 'Loading inventory' }) {
  return (
    <div className="loading-panel" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}…
    </div>
  )
}

export function Resource({ resource, label, children }) {
  if (resource.loading) return <Loading label={label} />
  if (resource.error) return <ErrorState error={resource.error} retry={resource.retry} />
  return children(resource.data)
}

export function PanelHeading({ title, subtitle, children }) {
  return (
    <header className="panel-heading">
      <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
      {children}
    </header>
  )
}

export function StatCard({ icon: Icon, tone, label, value, description }) {
  return (
    <article className="stat-card">
      <div className="stat-top"><span className={'stat-icon tone-' + tone}><Icon size={20} /></span><span>{label}</span></div>
      <p className="stat-value">{value}</p>
      <p className="stat-description">{description}</p>
    </article>
  )
}

export function SearchField({ value, onChange, label, placeholder }) {
  return (
    <label className="search-field">
      <span className="sr-only">{label}</span>
      <Search size={18} aria-hidden="true" />
      <input type="search" value={value} placeholder={placeholder} maxLength={150}
        onChange={event => onChange(event.target.value)} />
    </label>
  )
}

export function DataTable({ columns, label, children }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex="0">
      <table aria-label={label}>
        <thead><tr>{columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export function Pagination({ page, pageSize, total, onChange, noun }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const first = (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)
  const range = first === last ? String(first) : first + '–' + last
  if (total === 0) return null
  return (
    <div className="pagination">
      <p>Showing {range} of {total} {total === 1 ? noun.slice(0, -1) : noun}</p>
      <nav aria-label={noun + ' pagination'} className="pagination-controls">
        <button type="button" className="icon-button" aria-label="Previous page" disabled={page <= 1}
          onClick={() => onChange(page - 1)}><ChevronLeft size={17} /></button>
        <span>Page {page} of {pageCount}</span>
        <button type="button" className="icon-button" aria-label="Next page" disabled={page >= pageCount}
          onClick={() => onChange(page + 1)}><ChevronRight size={17} /></button>
      </nav>
    </div>
  )
}

export function ItemName({ name, category }) {
  return (
    <div className="item-name">
      <span className="item-icon"><Box size={17} strokeWidth={1.75} /></span>
      <div><strong>{name}</strong>{category && <small>{category}</small>}</div>
    </div>
  )
}

export function LotTable({ items, onEdit, onArchive, showSource = true }) {
  const columns = ['Ingredient', 'On hand', 'Expiry date', 'Status']
  if (showSource) columns.push('Source')
  if (onEdit || onArchive) columns.push('Actions')
  return (
    <DataTable label="Stock lots" columns={columns}>
      {items.map(lot => {
        const status = lotStatus(lot)
        return (
          <tr key={lot.id}>
            <td><ItemName name={lot.ingredient.name} category={lot.ingredient.category +
              (!showSource && lot.source === 'csv_import' ? ' · Imported demo' : '')} /></td>
            <td className="nowrap"><strong>{quantity(lot.quantity)}</strong> <span className="muted">{lot.ingredient.unit}</span></td>
            <td className="nowrap">{lot.expiry_date ? formatDate(lot.expiry_date) : 'Unknown'}
              {!lot.expiry_date && lot.source_expiry_text && <small className="cell-note">CSV value: {lot.source_expiry_text}</small>}</td>
            <td><Badge tone={status.tone}>{status.label}</Badge></td>
            {showSource && <td>{lot.source === 'csv_import' ? <Badge tone="violet">Imported demo</Badge> : <span className="muted">Manual</span>}</td>}
            {(onEdit || onArchive) && <td><RowActions itemName={lot.ingredient.name + ' lot ' + lot.id}
              onEdit={onEdit && (() => onEdit(lot))} onArchive={onArchive && (() => onArchive(lot))} /></td>}
          </tr>
        )
      })}
    </DataTable>
  )
}

export function RowActions({ itemName, onEdit, onArchive }) {
  return (
    <div className="row-actions">
      {onEdit && <button type="button" className="icon-button quiet" aria-label={'Edit ' + itemName}
        title={'Edit ' + itemName} onClick={onEdit}><Pencil size={16} /></button>}
      {onArchive && <button type="button" className="icon-button quiet danger-icon" aria-label={'Archive ' + itemName}
        title={'Archive ' + itemName} onClick={onArchive}><Trash2 size={16} /></button>}
    </div>
  )
}

export function HistoryTable({ items }) {
  return (
    <DataTable label="Stock transactions" columns={['Ingredient', 'Transaction', 'Quantity change', 'Balance', 'Date']}>
      {items.map(event => {
        const style = eventTypes[event.kind] ?? { label: event.kind, tone: 'neutral' }
        const change = Number(event.quantity_change)
        return (
          <tr key={event.id}>
            <td><ItemName name={event.ingredient_name || 'Ingredient ' + event.ingredient_id} category={'Lot #' + event.lot_id} /></td>
            <td><Badge tone={style.tone}>{style.label}</Badge>
              {event.expiry_before !== event.expiry_after && event.kind === 'updated' &&
                <small className="cell-note">Expiry {formatDate(event.expiry_before)} to {formatDate(event.expiry_after)}</small>}</td>
            <td className={'nowrap change ' + (change < 0 ? 'negative' : change > 0 ? 'positive' : '')}>
              {change > 0 && <ArrowDownLeft size={14} />}{change < 0 && <ArrowUpRight size={14} />}{changeLabel(event.quantity_change)} {event.unit}
            </td>
            <td className="nowrap">{quantity(event.quantity_after)} {event.unit}</td>
            <td className="nowrap">{formatDate(event.occurred_at)}<small className="cell-note">{formatTime(event.occurred_at)}</small></td>
          </tr>
        )
      })}
    </DataTable>
  )
}

export const alertGroups = [
  { key: 'near_expiry', label: 'Near expiry', icon: Clock3, tone: 'amber' },
  { key: 'expired', label: 'Expired stock', icon: AlertCircle, tone: 'red' },
  { key: 'low_stock', label: 'Low stock', icon: ClipboardList, tone: 'amber' },
  { key: 'unknown_expiry', label: 'Unknown expiry', icon: CircleHelp, tone: 'neutral' },
]
