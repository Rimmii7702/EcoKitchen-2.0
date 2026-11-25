import { useEffect, useRef, useState } from 'react'

export const apiBase = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:5000').replace(/\/+$/, '')
export const inventoryPath = '/api/inventory'
export const sessionExpired = 'ecokitchen-session-expired'
let csrfToken = null

export function setCsrfToken(token) {
  csrfToken = token
}

export function queryPath(path, params = {}) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== '' && value != null) query.set(key, value)
  }
  return query.size ? path + '?' + query : path
}

export async function request(path, { method = 'GET', body, signal, timeoutMs = 12000, service = 'inventory' } = {}) {
  let response
  const timeout = AbortSignal.timeout(timeoutMs)
  const multipart = body instanceof FormData
  const token = csrfToken
  const headers = { Accept: 'application/json' }
  if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json'
  if (path === '/api/auth/login') headers['X-EcoKitchen-Login'] = '1'
  else if (token && !['GET', 'HEAD', 'OPTIONS'].includes(method)) headers['X-CSRF-Token'] = token
  try {
    response = await fetch(apiBase + path, {
      method,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      credentials: 'include',
      headers,
      body: body === undefined || multipart ? body : JSON.stringify(body),
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new Error(timeout.aborted
      ? 'The ' + service + ' API took too long to respond.'
      : 'Could not reach the ' + service + ' API. Check that Flask is running at ' + (apiBase || window.location.origin) + '.')
  }
  if (response.status === 204) return null
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 && token && token === csrfToken && path !== '/api/auth/login') {
      csrfToken = null
      window.dispatchEvent(new Event(sessionExpired))
    }
    const error = new Error(data?.error || 'The ' + service + ' API returned HTTP ' + response.status + '.')
    error.status = response.status
    throw error
  }
  if (data === null) throw new Error('The ' + service + ' API returned an unexpected response.')
  return data
}

export function usePostAction({ service, timeoutMs, keepResult = false }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const active = useRef(null)
  useEffect(() => () => active.current?.abort(), [])

  async function run(path, body) {
    if (active.current) return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError(null)
    if (!keepResult) setData(null)
    try {
      const result = await request(path, { method: 'POST', body, signal: controller.signal, service, timeoutMs })
      if (!controller.signal.aborted) {
        setData(result)
        return result
      }
    } catch (error) {
      if (!controller.signal.aborted) setError(error)
    } finally {
      if (!controller.signal.aborted) {
        active.current = null
        setBusy(false)
      }
    }
  }

  function reset() {
    setData(null)
    setError(null)
  }

  return { data, error, busy, run, reset }
}

export async function ingredientOptions(signal) {
  const items = []
  const limit = 100
  let total
  do {
    const page = await request(queryPath(inventoryPath + '/ingredients', {
      limit, offset: items.length,
    }), { signal })
    items.push(...page.items)
    total = page.total
    if (!page.items.length) break
  } while (items.length < total)
  return items.sort((a, b) => a.name.localeCompare(b.name) || a.category.localeCompare(b.category))
}

export function useResource(load, revision) {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState({})
  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal).then(
      data => {
        if (!controller.signal.aborted) setState({ load, revision, attempt, data })
      },
      error => {
        if (!controller.signal.aborted) setState({ load, revision, attempt, error: error.message })
      },
    )
    return () => controller.abort()
  }, [load, revision, attempt])
  const current = state.load === load && state.revision === revision && state.attempt === attempt
  return {
    data: current ? state.data : undefined,
    error: current ? state.error : undefined,
    loading: !current,
    retry: () => setAttempt(value => value + 1),
  }
}
