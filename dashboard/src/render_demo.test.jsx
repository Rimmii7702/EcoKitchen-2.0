import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'

const viewer = { user: { username: 'demo-viewer', role: 'viewer' }, public_demo: true, csrf_token: null }
const reply = data => ({ status: 200, ok: true, json: async () => data })
const empty = { items: [], total: 0 }

afterEach(() => vi.unstubAllGlobals())

it('opens the fictional demo directly with read-only navigation', async () => {
  const user = userEvent.setup()
  window.history.replaceState(null, '', '/#inventory')
  const fetch = vi.fn(async url => {
    if (url.endsWith('/session')) return reply(viewer)
    if (url.endsWith('/recommendations')) {
      return reply({ near_expiry: [], low_stock: [], expiry_review: [], expired: [], as_of: '2026-11-25' })
    }
    return reply(empty)
  })
  vi.stubGlobal('fetch', fetch)
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'Inventory' })).toBeInTheDocument()
  expect(screen.getByText(/Data resets after the service sleeps/)).toBeInTheDocument()
  expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Add stock lot' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Open Flask UI' })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Kitchen heatmap' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('link', { name: 'Smart Recommendations' }))
  expect(await screen.findByText(/what-if stock and cost calculator/)).toBeInTheDocument()
  expect(fetch.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
})

it('opens only the verified historical benchmark in the demo forecasting page', async () => {
  window.history.replaceState(null, '', '/#forecasting?view=waste')
  const fetch = vi.fn(async url => url.endsWith('/session') ? reply(viewer) : new Promise(() => {}))
  vi.stubGlobal('fetch', fetch)
  render(<App />)
  expect(await screen.findByText(/Loading retail benchmark/)).toBeInTheDocument()
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/api/forecasting/retail-benchmark'))).toBe(true)
  expect(screen.queryByRole('button', { name: 'Waste prediction' })).not.toBeInTheDocument()
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/api/forecasting'))).toBe(false)
})
