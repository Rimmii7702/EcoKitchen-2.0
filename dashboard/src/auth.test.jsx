import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'

const signedOut = { user: null, csrf_token: null }
const admin = { user: { username: 'test-admin', role: 'admin' }, csrf_token: 'test-csrf' }
const viewer = { user: { username: 'test-viewer', role: 'viewer' }, csrf_token: 'viewer-csrf' }
const reply = (data, status = 200) => ({ status, ok: status < 400, json: async () => data })

afterEach(() => vi.unstubAllGlobals())

it('logs in with credentials, keeps the target view, and sends CSRF when signing out', async () => {
  const user = userEvent.setup()
  window.history.replaceState(null, '', '/#inventory')
  const fetch = vi.fn(async url => {
    if (url.endsWith('/session')) return reply(signedOut)
    if (url.endsWith('/login')) return reply(admin)
    if (url.endsWith('/logout')) return reply(null, 204)
    return reply({ items: [], total: 0 })
  })
  vi.stubGlobal('fetch', fetch)
  render(<App />)
  await screen.findByRole('heading', { name: 'Sign in to EcoKitchen' })
  await user.type(screen.getByLabelText('Username'), 'test-admin')
  await user.type(screen.getByLabelText('Password'), 'fixture-password')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByRole('heading', { name: 'Inventory' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Add stock lot' })).toBeInTheDocument()
  expect(fetch.mock.calls.find(([url]) => url.endsWith('/login'))[1]).toMatchObject({
    credentials: 'include', headers: { 'X-EcoKitchen-Login': '1' },
  })
  await user.click(screen.getByRole('button', { name: 'Sign out' }))
  expect(await screen.findByRole('heading', { name: 'Sign in to EcoKitchen' })).toBeInTheDocument()
  expect(fetch.mock.calls.find(([url]) => url.endsWith('/logout'))[1]).toMatchObject({
    credentials: 'include', headers: { 'X-CSRF-Token': admin.csrf_token },
  })
})

it('keeps viewer inventory read-only and returns to login on session expiry', async () => {
  const user = userEvent.setup()
  window.history.replaceState(null, '', '/#inventory')
  let expired = false
  const fetch = vi.fn(async url => {
    if (url.endsWith('/session')) return reply(viewer)
    return expired ? reply({ error: 'Sign in again.' }, 401) : reply({ items: [], total: 0 })
  })
  vi.stubGlobal('fetch', fetch)
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'Inventory' })).toBeInTheDocument()
  expect(screen.getByText(/Viewer access is read only/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Add stock lot' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('link', { name: 'Spoilage checks' }))
  expect(await screen.findByText('Vision uploads require an administrator.')).toBeInTheDocument()
  expired = true
  await user.click(screen.getByRole('link', { name: 'Inventory' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Your session expired')
})

it('shows a failed sign-in without letting the dashboard load', async () => {
  const user = userEvent.setup()
  const fetch = vi.fn(async url => url.endsWith('/session') ? reply(signedOut) :
    reply({ error: 'Invalid username or password.' }, 401))
  vi.stubGlobal('fetch', fetch)
  render(<App />)
  await user.type(await screen.findByLabelText('Username'), 'bad-user')
  await user.type(screen.getByLabelText('Password'), 'bad-password')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password.')
  expect(screen.queryByRole('navigation', { name: 'Dashboard navigation' })).not.toBeInTheDocument()
})
