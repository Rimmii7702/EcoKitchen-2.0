const quantityFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 })
const dateFormatter = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
const longDateFormatter = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
const timeFormatter = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })

export function quantity(value) {
  return quantityFormatter.format(Number(value))
}

export function formatDate(value, long = false) {
  if (!value) return '—'
  const date = new Date(value.length === 10 ? value + 'T00:00:00' : value)
  return (long ? longDateFormatter : dateFormatter).format(date)
}

export function formatTime(value) {
  return timeFormatter.format(new Date(value))
}

export function lotStatus(lot) {
  const days = lot.days_until_expiry
  if (Number(lot.quantity) === 0) return { label: 'Out of stock', tone: 'neutral' }
  if (days == null) return { label: 'Unknown expiry', tone: 'neutral' }
  if (days < 0) return { label: 'Expired', tone: 'red' }
  if (days <= 7) return { label: days === 0 ? 'Expires today' : 'Near expiry', tone: 'amber' }
  return { label: 'In date', tone: 'green' }
}

export function changeLabel(change) {
  return (Number(change) > 0 ? '+' : '') + quantity(change)
}

export const eventTypes = {
  imported: { label: 'CSV import', tone: 'violet' },
  created: { label: 'Lot added', tone: 'green' },
  updated: { label: 'Stock updated', tone: 'blue' },
  archived: { label: 'Lot archived', tone: 'neutral' },
}
