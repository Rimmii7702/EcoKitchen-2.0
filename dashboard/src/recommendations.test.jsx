import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './dashboard_test_fixture'

const rice = { id: 2, name: 'Rice', category: 'Pantry', unit: 'kg', eligible_quantity: '2.000', minimum_stock: '3.000' }
const beans = { id: 3, name: 'Beans', category: 'Pantry', unit: 'kg', eligible_quantity: '1.000', minimum_stock: '2.000' }
const early = { id: 11, ingredient: rice, ingredient_id: 2, quantity: '1.000', expiry_date: '2026-11-24', source: 'manual' }
const demo = { id: 12, ingredient: beans, ingredient_id: 3, quantity: '1.000', expiry_date: '2026-11-25', source: 'csv_import' }
const manualPrice = {
  basis: 'recorded_manual_lots', unit_cost: '2.125', estimated_cost: '2.125000', evidence: [{ lot_id: 11, unit_cost: '2.125' }],
}
const snapshot = {
  as_of: '2026-11-24', near_expiry_days: 7, ingredients: [rice, beans], recipe_ingredient_ids: [2, 3],
  near_expiry: [early, demo],
  expiry_review: [{ ...demo, id: 13, expiry_date: null, source_expiry_text: 'bad date' }],
  expired: [{ ...early, id: 14, expiry_date: '2026-11-23' }],
  low_stock: [
    { ...rice, replenishment_quantity: '1.000', eligible_lots: [early], cost: manualPrice },
    { ...beans, replenishment_quantity: '1.000', eligible_lots: [demo], cost: {
      basis: 'no_eligible_manual_lots', unit_cost: null, estimated_cost: null, evidence: [],
    } },
  ],
}

const response = (data, status = 200) => Promise.resolve({ status, ok: status < 400, json: async () => data })

function visit(fetch) {
  vi.stubGlobal('fetch', fetch)
  window.history.replaceState(null, '', '/#recommendations')
  render(<App />)
}

afterEach(() => vi.unstubAllGlobals())

it('explains FEFO, replenishment, source records and excluded lots', async () => {
  visit(vi.fn(() => response(snapshot)))
  expect(screen.getByRole('link', { name: 'Smart Recommendations' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('status')).toHaveTextContent('Loading recommendations')
  const near = await screen.findByRole('table', { name: 'Use first lots' })
  const rows = within(near).getAllByRole('row')
  expect(rows[1]).toHaveTextContent('Lot #11')
  expect(rows[2]).toHaveTextContent('Imported demo')
  expect(screen.getByRole('table', { name: 'Verify expiry dates lots' })).toHaveTextContent('bad date')
  expect(screen.getByRole('table', { name: 'Review expired stock lots' })).toHaveTextContent('Lot #14')
  const replenishment = screen.getByRole('table', { name: 'Rule-based replenishment' })
  expect(within(replenishment).getAllByText('Stock records', { selector: 'summary' })).toHaveLength(2)
  expect(replenishment).toHaveTextContent('2.125 in recorded cost units')
  expect(replenishment).toHaveTextContent('No eligible manual lots with cost evidence')
})

it('requests an explicit simulation and labels hypothetical allocations and the assumed cost', async () => {
  const user = userEvent.setup()
  const fetch = vi.fn((_url, options) => options.method === 'GET' ? response(snapshot) : response({
    simulation: true, ingredient: rice, requested_consumption: '1.500', eligible_quantity: '2.000',
    projected_quantity: '0.500', unfulfilled_quantity: '0.000', replenishment_quantity: '2.500',
    allocations: [{ ...early, allocated_quantity: '1.000' }],
    cost: { basis: 'simulation_assumption', estimated_cost: '7.500000', unit_cost: '3.000', evidence: [] },
  }))
  visit(fetch)
  await user.selectOptions(await screen.findByLabelText('Ingredient', { exact: true }), '2')
  const consumption = screen.getByLabelText('Hypothetical consumption (kg)')
  await user.clear(consumption)
  await user.type(consumption, '1.5')
  await user.type(screen.getByLabelText(/Assumed unit cost/), '3')
  expect(fetch).toHaveBeenCalledTimes(1)
  await user.click(screen.getByRole('button', { name: 'Run simulation' }))
  expect(await screen.findByText('Simulation', { exact: true })).toBeInTheDocument()
  expect(screen.getByText(/Simulated unit cost/)).toBeInTheDocument()
  expect(screen.getByRole('list', { name: 'Simulated FEFO allocations' })).toHaveTextContent('Lot #11')
  const [url, options] = fetch.mock.calls[1]
  expect(url).toMatch(/\/api\/recommendations\/simulate$/)
  expect(JSON.parse(options.body)).toEqual({ ingredient_id: 2, consume_quantity: '1.5', assumed_unit_cost: '3' })
  await user.clear(consumption)
  expect(screen.queryByText('Simulation', { exact: true })).not.toBeInTheDocument()
})

it('uses existing recipe API explicitly and displays a missing Gemini error', async () => {
  const user = userEvent.setup()
  const fetch = vi.fn((_url, options) => options.method === 'GET' ? response(snapshot) : response({
    error: 'Configure GEMINI_API_KEY to enable recipe generation.',
  }, 503))
  visit(fetch)
  const recipe = await screen.findByRole('button', { name: 'Generate priority recipe' })
  expect(fetch).toHaveBeenCalledTimes(1)
  await user.click(recipe)
  const error = await screen.findByRole('alert')
  expect(error).toHaveTextContent('Feature unavailable')
  expect(error).toHaveTextContent('GEMINI_API_KEY')
  const [url, options] = fetch.mock.calls[1]
  expect(url).toMatch(/\/api\/ai\/recipes$/)
  expect(JSON.parse(options.body)).toEqual({ ingredient_ids: [2, 3], near_expiry_only: true })
  expect(screen.queryByText('Gemini suggestion')).not.toBeInTheDocument()
})

it('offers a retry when the recommendation API is unavailable', async () => {
  const user = userEvent.setup()
  const fetch = vi.fn().mockImplementationOnce(() => response({ error: 'Initialize the inventory database.' }, 503))
    .mockImplementation(() => response(snapshot))
  visit(fetch)
  expect(await screen.findByRole('alert')).toHaveTextContent('Initialize the inventory database.')
  await user.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByRole('table', { name: 'Rule-based replenishment' })).toBeInTheDocument()
})
