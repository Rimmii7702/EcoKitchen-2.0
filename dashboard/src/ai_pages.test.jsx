import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './dashboard_test_fixture'

const rice = {
  id: 2, name: 'Rice', category: 'Pantry', unit: 'kg', available_quantity: '3.250',
  earliest_expiry: '2026-11-26', near_expiry: true, sources: ['csv_import', 'manual'],
}
const saved = {
  date: '2025-06-20',
  todays_special: { dish: { name: 'Saved lentil special', ingredients: ['Lentils'], recipe: 'Simmer the lentils.' } },
  two_week_menu: { dish: { name: 'Saved weekly dish', ingredients: ['Rice'] } },
}
const fresh = {
  id: 'menu-new', date: '2026-11-24', generated_at: '2026-11-24T14:00:00Z',
  todays_special: { dish: { name: 'Fresh rice special', ingredients: ['Rice'] } }, two_week_menu: {},
}
const previous = {
  id: 'menu-old', date: '2026-11-20', todays_special: { dish: { name: 'Earlier rice special' } }, two_week_menu: {},
}

const response = (data, status = 200) => Promise.resolve({ status, ok: status < 400, json: async () => data })
const show = hash => window.history.replaceState(null, '', '/#' + hash)
const upload = () => new File(['fixture'], 'photo.jpg', { type: 'image/jpeg' })

describe('Dashboard Gemini workflows', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn().mockReturnValue('blob:ai-preview')
    URL.revokeObjectURL = vi.fn()
  })
  afterEach(() => vi.unstubAllGlobals())

  it('labels SQLite and AI data separately and sends selected ingredient IDs only after submission', async () => {
    show('recipes')
    const user = userEvent.setup()
    const fetch = vi.fn((url, options) => options.method === 'GET'
      ? response({ items: [rice], as_of: '2026-11-24', near_expiry_days: 7, selection_limit: 20 })
      : response({ inventory_inputs: [rice], recipe: {
        name: 'Lemon rice', ingredients: ['200 g rice'], steps: ['Cook the rice.'],
        waste_reduction_tips: ['Use the older rice lot first.'],
      } }))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    const option = await screen.findByRole('checkbox', { name: /Rice/ })
    expect(screen.getByRole('heading', { name: 'Available from SQLite' })).toBeInTheDocument()
    expect(screen.getByText('Imported demo')).toBeInTheDocument()
    expect(screen.getByText('Near expiry')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate recipe' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Select near expiry' }))
    expect(option).toBeChecked()
    expect(fetch).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Generate recipe' }))
    expect(await screen.findByRole('heading', { name: 'Lemon rice' })).toBeInTheDocument()
    expect(screen.getByText('Gemini suggestion')).toBeInTheDocument()
    expect(screen.getByText('SQLite ingredients sent')).toBeInTheDocument()
    expect(screen.getByText('Cook the rice.')).toBeInTheDocument()
    expect(screen.getByText('Use the older rice lot first.')).toBeInTheDocument()
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ ingredient_ids: [2] })
  })

  it('loads and switches dated snapshots without generation until explicitly requested', async () => {
    show('specials')
    const user = userEvent.setup()
    let generated = false
    const fetch = vi.fn((url, options) => {
      if (options.method === 'POST') {
        generated = true
        return response({ latest_snapshot: fresh })
      }
      return response({ saved_snapshot: saved, latest_snapshot: generated ? fresh : null,
        previous_snapshots: generated ? [previous] : [] })
    })
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Saved lentil special' })).toBeInTheDocument()
    expect(screen.getByText(/Generated.*2025/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Two-week menu' }))
    expect(screen.getByRole('heading', { name: 'Saved weekly dish' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Specials' }))
    expect(fetch.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Generate fresh menu' }))
    expect(await screen.findByRole('heading', { name: 'Fresh rice special' })).toBeInTheDocument()
    expect(screen.getByText(/Generated.*2026/)).toBeInTheDocument()
    const picker = await screen.findByRole('combobox', { name: 'Snapshot' })
    await user.selectOptions(picker, 'menu-old')
    expect(screen.getByRole('heading', { name: 'Earlier rice special' })).toBeInTheDocument()
    await user.selectOptions(picker, 'saved')
    expect(screen.getByRole('heading', { name: 'Saved lentil special' })).toBeInTheDocument()
    expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
  })

  it('keeps the saved menu visible when Gemini is unavailable', async () => {
    show('specials')
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn((url, options) => options.method === 'POST'
      ? response({ error: 'Set GEMINI_API_KEY to enable Gemini features.' }, 503)
      : response({ saved_snapshot: saved, latest_snapshot: null })))
    render(<App />)
    await screen.findByRole('heading', { name: 'Saved lentil special' })
    await user.click(screen.getByRole('button', { name: 'Generate fresh menu' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('GEMINI_API_KEY')
    expect(screen.getByRole('heading', { name: 'Saved lentil special' })).toBeInTheDocument()
  })

  it('marks withheld annotated waste analysis unavailable and sends no requests', () => {
    show('waste')
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect(screen.getByText(/Annotated waste-photo analysis is unavailable/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Analyze waste' })).not.toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('labels heatmap values as simulated and shows both generated assets', async () => {
    show('heatmap')
    const user = userEvent.setup()
    const fetch = vi.fn().mockImplementation(() => response({
      stations: [{ area: 'Prep station', waste_level: 'Medium', waste_kg: 5 }],
      simulated_total_kg: 5,
      heatmap_url: '/api/vision/media/fixture/kitchen_heatmap.jpg',
      distribution_url: '/api/vision/media/fixture/kitchen_distribution.jpg',
    }))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    await user.upload(screen.getByLabelText(/Choose an image/), upload())
    expect(fetch).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Generate heatmap' }))
    expect(await screen.findByText('Simulated waste values')).toBeInTheDocument()
    expect(screen.getByAltText('Simulated kitchen heatmap')).toBeInTheDocument()
    expect(screen.getByAltText('Simulated waste distribution')).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Simulated kitchen station waste' })
    expect(within(table).getByText('Prep station')).toBeInTheDocument()
    expect(within(table).getByText('5 kg')).toBeInTheDocument()
  })
})