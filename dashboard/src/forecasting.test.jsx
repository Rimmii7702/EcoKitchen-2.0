import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './dashboard_test_fixture'

const demo = {
  available: false, reason: 'The sales sample has uneven coverage.',
  history: [{ week_start: '2025-01-01', week_end: '2025-01-05', units: 50, row_count: 1, complete_period: false }],
  source: { name: 'fictional-weekly-sales.csv', kind: 'self_made_demo' },
  coverage: { rows: 56, periods: 15, min_rows_per_period: 1, max_rows_per_period: 6, max_rows_per_dish: 2 },
}
const wasteUnavailable = {
  available: false, reason: 'No measured waste targets are included.',
  simulation_available: false, simulation_reason: 'Run local training to enable simulation.',
}
const fields = {
  stock_quantity: { min: 50, max: 499, step: 1 },
  sales_volume: { min: 10, max: 449, step: 1 },
  days_to_expire: { min: 1, max: 14, step: 1 },
  unit_price: { min: 1, max: 15, step: 0.01 },
  inventory_turnover_rate: { min: 0.1, max: 1.5, step: 0.01 },
  category: { options: ['Fruit'] },
}
const reply = (data, status = 200) => Promise.resolve({ status, ok: status < 400, json: async () => data })

function renderForecasting(fetch) {
  window.history.replaceState(null, '', '/#forecasting')
  vi.stubGlobal('fetch', fetch)
  render(<App />)
}

describe('Forecasting dashboard', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows self-made history and both missing-model states without making a forecast', async () => {
    const user = userEvent.setup()
    const fetch = vi.fn().mockImplementation(() => reply({ demand: demo, waste: wasteUnavailable }))
    renderForecasting(fetch)
    expect(await screen.findByRole('heading', { name: 'Forecast unavailable' })).toBeInTheDocument()
    expect(screen.getByText('Self-made demo data')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /demo recorded sales/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Forecast next week' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Forecasting' })).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Waste prediction' }))
    expect(screen.getByRole('heading', { name: 'Measured waste prediction unavailable' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Simulation model unavailable' })).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('renders test metrics and requests the next week from an available model', async () => {
    const user = userEvent.setup()
    const fetch = vi.fn((url, options) => options.method === 'POST'
      ? reply({ predicted_units: 321.25, week_start: '2025-01-13', week_end: '2025-01-19', model: 'ridge' })
      : reply({
        demand: {
          ...demo, available: true, coverage: null, history: [{ week_start: '2025-01-06', week_end: '2025-01-12', units: 320 }],
          source: { name: 'Verified test export', kind: 'operator_verified_weekly_sales' },
          report: {
            selected: 'ridge', algorithms: { ridge: 'Ridge', naive: 'Last observed week' },
            split: { test: { weeks: 5, from: '2025-01-06', through: '2025-01-06' } },
            test: { ridge: { mae: 2, rmse: 3, wape_pct: 1.5 }, naive: { mae: 4, rmse: 6, wape_pct: 2.5 } },
          },
        },
        waste: wasteUnavailable,
      }))
    renderForecasting(fetch)
    const metrics = await screen.findByRole('table', { name: 'Demand forecasting evaluation' })
    expect(within(metrics).getByText('Last observed week')).toBeInTheDocument()
    expect(screen.getByText('Operator-verified export')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Forecast next week' }))
    expect(await screen.findByText('321.25 units')).toBeInTheDocument()
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ horizon_weeks: 1 })
  })

  it('validates inputs and distinguishes simulated waste results from observations', async () => {
    const user = userEvent.setup()
    const fetch = vi.fn((url, options) => options.method === 'POST'
      ? reply({ simulated_waste_units: 47.5, source_kind: 'synthetic' })
      : reply({ demand: demo, waste: {
        ...wasteUnavailable, simulation_available: true, fields,
        report: {
          split: { test_samples: 200 }, algorithms: { mean: 'Training mean' },
          test: { mean: { mae: 94, rmse: 113, r2: -0.0001 } },
        },
      } }))
    renderForecasting(fetch)
    await screen.findByRole('heading', { name: 'Forecast unavailable' })
    await user.click(screen.getByRole('button', { name: 'Waste prediction' }))
    await user.type(screen.getByLabelText('Stock quantity'), '120')
    await user.type(screen.getByLabelText('Assumed sales volume'), '45')
    await user.type(screen.getByLabelText('Days to expiry'), '0')
    await user.type(screen.getByLabelText('Unit price'), '5.5')
    await user.type(screen.getByLabelText('Inventory turnover rate'), '0.6')
    await user.selectOptions(screen.getByLabelText('Category'), 'Fruit')
    await user.click(screen.getByRole('button', { name: 'Run simulation' }))
    expect(fetch).toHaveBeenCalledTimes(1)
    await user.clear(screen.getByLabelText('Days to expiry'))
    await user.type(screen.getByLabelText('Days to expiry'), '5')
    await user.click(screen.getByRole('button', { name: 'Run simulation' }))
    expect(await screen.findByText('47.5')).toBeInTheDocument()
    expect(screen.getByText('Synthetic result')).toBeInTheDocument()
    expect(screen.getByText('Simulated waste units')).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Synthetic waste evaluation' })).toBeInTheDocument()
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toMatchObject({ category: 'Fruit', days_to_expire: 5 })
  })

  it('shows a prediction error without inventing output when an artifact disappears', async () => {
    const user = userEvent.setup()
    renderForecasting(vi.fn((url, options) => options.method === 'POST'
      ? reply({ error: 'The demand artifact is unavailable.' }, 503)
      : reply({ demand: { ...demo, available: true }, waste: wasteUnavailable })))
    await user.click(await screen.findByRole('button', { name: 'Forecast next week' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The demand artifact is unavailable.')
    expect(screen.queryByText('Next-week forecast')).not.toBeInTheDocument()
  })
})