import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './dashboard_test_fixture'
import scorecard from '../../forecasting/results/freshretailnet-holdout-v1.json'

const keys = ['seasonal_naive', 'lightgbm']
const daily = Object.groupBy(scorecard.daily_totals_by_24h_stockout, row => row.target_date)
const benchmark = {
  schema_version: 1,
  dataset: {
    name: 'FreshRetailNet-50K', publisher: 'Dingdong-Inc', domain: 'Fresh grocery retail',
    url: 'https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K', license: 'CC BY 4.0',
    license_url: 'https://creativecommons.org/licenses/by/4.0/',
  },
  evaluation: {
    forecast_origin: '2024-06-25', start: '2024-06-26', end: '2024-07-02', rows: 350000, pairs: 50000,
    horizon_days: 7, units: 'normalized retail sales units',
  },
  models: {
    seasonal_naive: { metrics: scorecard.seasonal_naive.overall.all },
    lightgbm: { trees: 96, metrics: scorecard.lightgbm.overall.all },
  },
  wape_improvement_pp: scorecard.wape_improvement_percentage_points,
  daily_sales: Object.entries(daily).map(([date, rows]) => ({
    date, ...Object.fromEntries(['actual', ...keys].map(key => [key, rows[0][key] + rows[1][key]])),
  })),
  horizons: Object.keys(daily).map((date, index) => ({
    date, day: index + 1, ...Object.fromEntries(keys.map(key => [key, scorecard[key].by_horizon[index + 1].all])),
  })),
  stockout_groups: [['in_stock_24h', 'Fully in stock'], ['stockout_24h', 'Stockout-affected']].map(([id, label]) => ({
    id, label, rows: scorecard.lightgbm.overall[id].scored,
    ...Object.fromEntries(keys.map(key => [key, scorecard[key].overall[id]])),
  })),
}
const existing = { demand: { available: false, history: [], reason: 'Add verified weekly sales.' } }
const reply = (data, status = 200) => Promise.resolve({ status, ok: status < 400, json: async () => data })

function visit(fetch, path = '/#forecasting?view=retail') {
  window.history.replaceState(null, '', path)
  vi.stubGlobal('fetch', fetch)
  render(<App />)
}

afterEach(() => vi.unstubAllGlobals())

it('renders the real holdout comparison, scope, and license from a shareable Forecasting link', async () => {
  const fetch = vi.fn(() => reply(benchmark))
  visit(fetch)
  await screen.findByRole('heading', { name: 'Retail Demand Benchmark' })
  const summary = within(screen.getByLabelText('LightGBM holdout summary'))
  for (const value of ['350,000', '7 days', '0.4014', '0.7098', '33.6450%', '-3.1445%']) {
    expect(summary.getByText(value)).toBeInTheDocument()
  }
  expect(screen.getByText('8.1864 pp')).toBeInTheDocument()
  expect(screen.getByText('Frozen 96-tree CPU LightGBM')).toBeInTheDocument()
  expect(screen.getByText('Completed official holdout')).toBeInTheDocument()
  expect(screen.getByText(/FreshRetailNet-50K from Dingdong-Inc.*Jun 26.*Jul 2, 2024/)).toBeInTheDocument()
  const comparison = within(screen.getByRole('table', { name: 'Holdout model comparison' }))
  expect(within(comparison.getByRole('row', { name: /Seasonal naive/ })).getByText('41.8314%')).toBeInTheDocument()
  expect(within(comparison.getByRole('row', { name: /LightGBM/ })).getByText('33.6450%')).toBeInTheDocument()
  for (const value of ['151,944 evaluation records', '198,056 evaluation records', '29.9575%', '36.9191%']) {
    expect(screen.getByText(value)).toBeInTheDocument()
  }
  expect(screen.getByText(/Demand during stockouts remains unobserved/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'CC BY 4.0 license' })).toHaveAttribute('href', benchmark.dataset.license_url)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/api\/forecasting\/retail-benchmark$/), expect.objectContaining({ method: 'GET' }))
})

it('renders all chart dates and exposes the complete chart data through keyboard-accessible disclosures', async () => {
  const user = userEvent.setup()
  visit(vi.fn(() => reply(benchmark)))
  const sales = await screen.findByRole('img', { name: 'Daily observed and predicted sales' })
  expect(within(sales).getByText('Jun 26')).toBeInTheDocument()
  expect(within(sales).getByText('Jul 2')).toBeInTheDocument()
  expect(sales.querySelectorAll('circle')).toHaveLength(21)
  const horizon = screen.getByRole('img', { name: 'Seven-day WAPE comparison' })
  expect(horizon.querySelectorAll('circle')).toHaveLength(14)
  await user.click(screen.getByText('View daily sales data'))
  const dailyRows = within(screen.getByRole('table', { name: 'Daily observed and predicted sales data' })).getAllByRole('row')
  expect(dailyRows).toHaveLength(8)
  expect(dailyRows[1]).toHaveTextContent('53,094.64')
  await user.click(screen.getByText('View horizon WAPE data'))
  const horizonRows = within(screen.getByRole('table', { name: 'Seven-day WAPE comparison data' })).getAllByRole('row')
  expect(horizonRows).toHaveLength(8)
  expect(horizonRows[1]).toHaveTextContent('40.8119%')
  expect(horizonRows[1]).toHaveTextContent('31.8255%')
})

it('navigates from Forecasting, supports Back, and leaves the existing Demand tab intact', async () => {
  const user = userEvent.setup()
  visit(vi.fn(url => reply(url.endsWith('retail-benchmark') ? benchmark : existing)), '/#forecasting')
  await screen.findByRole('heading', { name: 'Forecast unavailable' })
  await user.click(screen.getByRole('button', { name: 'Retail Demand Benchmark' }))
  await screen.findByRole('heading', { name: 'Retail Demand Benchmark' })
  expect(window.location.hash).toBe('#forecasting?view=retail')
  expect(screen.getByRole('button', { name: 'Retail Demand Benchmark' })).toHaveAttribute('aria-pressed', 'true')
  window.history.back()
  await screen.findByRole('heading', { name: 'Forecast unavailable' })
  expect(screen.getByRole('button', { name: 'Demand' })).toHaveAttribute('aria-pressed', 'true')
})

it('shows loading, useful errors, retry, and an empty response state', async () => {
  let failRequest
  const fetch = vi.fn()
    .mockImplementationOnce(() => new Promise(resolve => { failRequest = resolve }))
    .mockImplementationOnce(() => reply({ daily_sales: [], horizons: [] }))
  visit(fetch)
  expect(screen.getByRole('status')).toHaveTextContent('Loading retail benchmark')
  failRequest({ status: 503, ok: false, json: async () => ({ error: 'The published scorecard is missing.' }) })
  expect(await screen.findByRole('alert')).toHaveTextContent('The published scorecard is missing.')
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByRole('heading', { name: 'No retail benchmark results' })).toBeInTheDocument()
  expect(screen.queryByRole('img', { name: 'Daily observed and predicted sales' })).not.toBeInTheDocument()
})
