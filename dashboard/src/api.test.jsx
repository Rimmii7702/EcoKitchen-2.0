import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { usePostAction } from './api'

const reply = (data, status = 200) => ({ status, ok: status < 400, json: async () => data })

afterEach(() => vi.unstubAllGlobals())

it.each([true, false])('preserves the selected retry behavior with keepResult=%s', async keepResult => {
  let finish
  const fetch = vi.fn()
    .mockResolvedValueOnce(reply({ id: 1 }))
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetch)
  const { result } = renderHook(() => usePostAction({ service: 'test', keepResult }))
  await act(async () => {
    expect(await result.current.run('/test', { count: 1 })).toEqual({ id: 1 })
  })
  let pending
  act(() => { pending = result.current.run('/test', { count: 2 }) })
  expect(result.current.busy).toBe(true)
  expect(result.current.data).toEqual(keepResult ? { id: 1 } : null)
  await act(async () => { expect(await result.current.run('/test', { count: 3 })).toBeUndefined() })
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ count: 2 })
  await act(async () => {
    finish(reply({ error: 'Unavailable' }, 503))
    await pending
  })
  expect(result.current.busy).toBe(false)
  expect(result.current.error).toMatchObject({ message: 'Unavailable', status: 503 })
  expect(result.current.data).toEqual(keepResult ? { id: 1 } : null)
  act(() => result.current.reset())
  expect(result.current.error).toBeNull()
  expect(result.current.data).toBeNull()
})

it('aborts an active request on unmount and ignores a late response', async () => {
  let finish
  const fetch = vi.fn(() => new Promise(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetch)
  const { result, unmount } = renderHook(() => usePostAction({ service: 'test' }))
  let pending
  act(() => { pending = result.current.run('/test', {}) })
  const signal = fetch.mock.calls[0][1].signal
  expect(signal.aborted).toBe(false)
  unmount()
  expect(signal.aborted).toBe(true)
  finish(reply({ id: 1 }))
  expect(await pending).toBeUndefined()
})
