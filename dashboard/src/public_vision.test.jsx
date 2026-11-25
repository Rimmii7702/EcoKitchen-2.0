import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './dashboard_test_fixture'

function response(data, status = 200) {
  return { status, ok: status < 400, json: async () => data }
}

function photo(name = 'photo.jpg') {
  return new File(['photo fixture'], name, { type: 'image/jpeg' })
}

describe('Dashboard vision', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/#detection')
    URL.createObjectURL = vi.fn().mockReturnValue('blob:upload-preview')
    URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => vi.unstubAllGlobals())

  it('retains completed Gemini checks and sends only the configured photo workflow', async () => {
    const user = userEvent.setup()
    const fetch = vi.fn().mockResolvedValue(response({
      analysis: [{ item: 'Pear', status: 'Soon-to-rot', confidence: '82', action: 'Use soon' }],
    }))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect(screen.getByText(/Image and video object detection are unavailable/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Video scan' })).not.toBeInTheDocument()
    await user.upload(screen.getByLabelText(/Choose an image/), photo('pear.jpg'))
    expect(await screen.findByAltText('Selected image preview')).toHaveAttribute('src', 'blob:upload-preview')
    expect(fetch).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Run spoilage check' }))
    const current = await screen.findByRole('heading', { name: 'Gemini spoilage assessment' })
    expect(within(current.closest('section')).getByText('82%')).toBeInTheDocument()
    expect(fetch.mock.calls[0][0]).toMatch(/\/api\/vision\/spoilage$/)
    const history = screen.getByRole('heading', { name: 'Recent spoilage checks' }).closest('section')
    await user.click(within(history).getByText('pear.jpg'))
    expect(within(history).getByText('Use soon')).toBeInTheDocument()
    expect(fetch.mock.calls[0][1].body.get('image').name).toBe('pear.jpg')
    expect(fetch.mock.calls[0][1].headers).not.toHaveProperty('Content-Type')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('rejects unsupported formats and oversized files before upload', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    const input = screen.getByLabelText(/Choose an image/)
    await user.upload(input, new File(['svg'], 'diagram.svg', { type: 'image/svg+xml' }))
    expect(screen.getByRole('alert')).toHaveTextContent('supported formats')
    await user.upload(input, new File([new Uint8Array(10 * 1024 ** 2 + 1)], 'large.jpg'))
    expect(screen.getByRole('alert')).toHaveTextContent('10 MiB limit')
    expect(screen.getByRole('button', { name: 'Run spoilage check' })).toBeDisabled()
    expect(fetch).not.toHaveBeenCalled()
  })
})
