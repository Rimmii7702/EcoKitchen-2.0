import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './dashboard_test_fixture'

const rice = { id: 1, name: 'Rice', category: 'Pantry', unit: 'kg', minimum_stock: '1.000' }
const demoLot = {
  id: 1, ingredient: rice, ingredient_id: 1, quantity: '3.250', expiry_date: '2025-02-25',
  days_until_expiry: -300, cost_per_unit: null, days_after_restock: null, source: 'csv_import',
}
const transaction = {
  id: 11, lot_id: 1, ingredient_id: 1, ingredient_name: 'Rice', kind: 'imported', unit: 'kg',
  quantity_before: '0.000', quantity_after: '3.250', quantity_change: '3.250',
  expiry_before: null, expiry_after: '2025-02-25', occurred_at: '2026-11-24T12:00:00+00:00',
}

function reply(data, status = 200) {
  return Promise.resolve({ status, ok: status >= 200 && status < 300, json: async () => data })
}

function route(hash = '') {
  window.history.replaceState(null, '', '/' + hash)
}

function apiFixture(overrides = {}) {
  const lots = overrides.lots ?? [demoLot]
  const ingredients = overrides.ingredients ?? [rice]
  const history = overrides.history ?? [transaction]
  const alerts = overrides.alerts ?? {
    as_of: '2026-11-24', days: 7, near_expiry: [], expired: [demoLot], unknown_expiry: [],
    low_stock: [{ ...rice, available_quantity: '0.000' }],
  }
  return vi.fn((url, options) => {
    const { pathname, searchParams } = new URL(url)
    const method = options.method
    const body = options.body ? JSON.parse(options.body) : null
    const offset = Number(searchParams.get('offset') || '0')
    const limit = Number(searchParams.get('limit') || '100')
    const query = searchParams.get('q')?.toLowerCase()
    const source = searchParams.get('source')
    const matches = item => !query || item.name.toLowerCase().includes(query)
    const page = items => reply({ items: items.slice(offset, offset + limit), total: items.length })

    if (overrides.before) {
      const response = overrides.before({ pathname, method, body })
      if (response) return response
    }
    if (pathname === '/api/inventory/alerts') return reply({ ...alerts, days: Number(searchParams.get('days') || '7') })
    if (pathname === '/api/inventory/history') return page(history.filter(event =>
      !searchParams.has('lot_id') || event.lot_id === Number(searchParams.get('lot_id'))))
    if (pathname === '/api/inventory/ingredients') {
      if (method === 'GET') return page(ingredients.filter(matches))
      const item = { ...body, id: 300 }
      ingredients.unshift(item)
      return reply(item, 201)
    }
    if (pathname.startsWith('/api/inventory/ingredients/')) {
      const at = ingredients.findIndex(item => item.id === Number(pathname.split('/').at(-1)))
      if (method === 'DELETE') { ingredients.splice(at, 1); return reply(null, 204) }
      ingredients[at] = { ...ingredients[at], ...body }
      return reply(ingredients[at])
    }
    if (pathname === '/api/inventory') {
      if (method === 'GET') return page(lots.filter(lot => matches(lot.ingredient) && (!source || lot.source === source)))
      const lot = { ...demoLot, ...body, id: 99, ingredient: ingredients.find(item => item.id === body.ingredient_id),
        days_until_expiry: null, source: 'manual' }
      lots.unshift(lot)
      return reply(lot, 201)
    }
    if (pathname.startsWith('/api/inventory/')) {
      const at = lots.findIndex(item => item.id === Number(pathname.split('/').at(-1)))
      if (method === 'DELETE') { lots.splice(at, 1); return reply(null, 204) }
      lots[at] = { ...lots[at], ...body }
      return reply(lots[at])
    }
    return reply({ error: 'Unknown test route' }, 404)
  })
}

describe('EcoKitchen dashboard', () => {
  beforeEach(() => route())
  afterEach(() => vi.unstubAllGlobals())

  it('links to the legacy Flask route behind a same-origin proxy', async () => {
    vi.stubGlobal('fetch', apiFixture())
    render(<App />)
    expect(new URL(screen.getByRole('link', { name: 'Open Flask UI' }).href).pathname).toBe('/legacy/')
    await screen.findByRole('heading', { name: 'Imported demo inventory' })
  })

  it('renders real response counts, imported-demo context and navigable alerts', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', apiFixture())
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Imported demo inventory' })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Inventory indicators' })).getByText('1 ingredient in the catalog')).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Expired stock 1' }))
    const table = await screen.findByRole('table', { name: 'Stock lots' })
    expect(within(table).getByText('Imported demo')).toBeInTheDocument()
    expect(within(table).getByText('Expired')).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Low stock 1' }))
    expect(await screen.findByRole('table', { name: 'Low-stock ingredients' })).toBeInTheDocument()
  })

  it('paginates inventory, searches beyond the first page and filters by source', async () => {
    route('#inventory')
    const user = userEvent.setup()
    const lots = Array.from({ length: 11 }, (_, index) => ({
      ...demoLot, id: index + 1,
      ingredient: { ...rice, name: index === 10 ? 'Basil' : 'Rice ' + (index + 1) },
      source: index === 10 ? 'manual' : 'csv_import',
    }))
    const fetch = apiFixture({ lots })
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect(await screen.findByText('Showing 1–10 of 11 lots')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('Showing 11 of 11 lots')).toBeInTheDocument()
    await user.type(screen.getByRole('searchbox', { name: 'Search stock lots' }), 'Basil')
    expect(await screen.findByText('Showing 1 of 1 lot')).toBeInTheDocument()
    expect(screen.getByText('Basil')).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Source' }), 'csv_import')
    expect(await screen.findByRole('heading', { name: 'No lots found' })).toBeInTheDocument()
    expect(fetch.mock.calls.some(([url]) => {
      const params = new URL(url).searchParams
      return params.get('q') === 'Basil' && params.get('source') === 'csv_import'
    })).toBe(true)
  })

  it('creates, edits and archives stock lots through accessible forms', async () => {
    route('#inventory')
    const user = userEvent.setup()
    const fetch = apiFixture({ lots: [] })
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Add stock lot' }))
    const dialog = screen.getByRole('dialog', { name: 'Add stock lot' })
    await user.selectOptions(await within(dialog).findByLabelText('Ingredient'), '1')
    await user.type(within(dialog).getByLabelText('Quantity'), '4.250')
    await user.click(within(dialog).getByRole('button', { name: 'Save stock lot' }))
    expect(await screen.findByText('Stock lot added.')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Edit Rice lot 99' }))
    const edit = screen.getByRole('dialog', { name: 'Edit stock lot' })
    const field = within(edit).getByLabelText('Quantity (kg)')
    await user.clear(field)
    await user.type(field, '3')
    await user.click(within(edit).getByRole('button', { name: 'Save stock lot' }))
    expect(await screen.findByText('Stock lot updated.')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Archive Rice lot 99' }))
    const archive = screen.getByRole('dialog', { name: 'Archive stock lot' })
    await user.click(within(archive).getByRole('button', { name: 'Archive stock lot' }))
    expect(await screen.findByText('Stock lot archived.')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'No lots found' })).toBeInTheDocument()
    const writes = fetch.mock.calls.filter(([, args]) => args.method !== 'GET')
    expect(writes.map(([, args]) => args.method)).toEqual(['POST', 'PATCH', 'DELETE'])
    expect(JSON.parse(writes[1][1].body)).toEqual({ quantity: '3' })
  })

  it('shows validation errors without closing the ingredient form', async () => {
    route('#inventory')
    const user = userEvent.setup()
    let shouldFail = true
    const fetch = apiFixture({ before: ({ pathname, method }) => {
      if (pathname.endsWith('/ingredients') && method === 'POST' && shouldFail) {
        shouldFail = false
        return reply({ error: 'Ingredient already exists.' }, 409)
      }
    } })
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Ingredients' }))
    await user.click(screen.getByRole('button', { name: 'Add ingredient' }))
    const dialog = screen.getByRole('dialog', { name: 'Add ingredient' })
    await user.type(within(dialog).getByLabelText('Ingredient name'), 'Salt')
    await user.type(within(dialog).getByLabelText('Category'), 'Dry goods')
    await user.type(within(dialog).getByLabelText('Stock unit'), 'kg')
    await user.click(within(dialog).getByRole('button', { name: 'Save ingredient' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Ingredient already exists.')
    expect(within(dialog).getByLabelText('Ingredient name')).toHaveValue('Salt')
    await user.click(within(dialog).getByRole('button', { name: 'Save ingredient' }))
    expect(await screen.findByText('Ingredient added.')).toBeInTheDocument()
    expect(await screen.findByText('Salt')).toBeInTheDocument()
  })

  it('retries failed reads, renders history and shows missing demand results honestly', async () => {
    route('#history')
    const user = userEvent.setup()
    const workingFetch = apiFixture({ before: ({ pathname }) => {
      if (pathname === '/api/forecasting') return reply({
        demand: { available: false, reason: 'No trained forecast.', history: [] }, waste: {},
      })
    } })
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('offline')).mockImplementation(workingFetch))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the inventory API')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    const table = await screen.findByRole('table', { name: 'Stock transactions' })
    expect(within(table).getByText('CSV import')).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Forecasting' }))
    expect(await screen.findByRole('heading', { name: 'Forecast unavailable' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
