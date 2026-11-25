import { useEffect, useState } from 'react'
import { Sprout } from 'lucide-react'
import { isPublicDemoBuild, request, sessionExpired, setCsrfToken } from './api'
import { Loading } from './ui'
import './auth.css'

export function SessionGate({ children }) {
  const [identity, setIdentity] = useState(undefined)
  const [publicDemo, setPublicDemo] = useState(false)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  function accept(result) {
    setPublicDemo(result.public_demo === true)
    setCsrfToken(result.csrf_token)
    setIdentity(result.user)
  }

  useEffect(() => {
    const controller = new AbortController()
    const expire = () => {
      setIdentity(null)
      setNotice('Your session expired. Sign in again to continue.')
    }
    setCsrfToken(null)
    window.addEventListener(sessionExpired, expire)
    request('/api/auth/session', { signal: controller.signal, service: 'authentication' }).then(result => {
      if (!controller.signal.aborted) accept(result)
    }).catch(error => {
      if (!controller.signal.aborted) {
        setIdentity(null)
        setNotice(error.message)
      }
    })
    return () => {
      controller.abort()
      window.removeEventListener(sessionExpired, expire)
      setCsrfToken(null)
    }
  }, [])

  async function signIn(event) {
    event.preventDefault()
    if (busy) return
    const form = event.currentTarget
    const body = Object.fromEntries(new FormData(form))
    setBusy(true)
    setNotice('')
    try {
      accept(await request('/api/auth/login', { method: 'POST', body, service: 'authentication' }))
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function signOut() {
    if (busy) return
    setBusy(true)
    setNotice('')
    try {
      await request('/api/auth/logout', { method: 'POST', service: 'authentication' })
      accept({ user: null, csrf_token: null })
    } catch (error) {
      if (error.status !== 401) setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  if (identity === undefined) return <main className="auth-screen"><Loading label="Checking your session" /></main>
  if (identity && (!isPublicDemoBuild || (publicDemo && identity.role === 'viewer'))) {
    return children({ user: identity, signOut, authError: notice, signingOut: busy, publicDemo })
  }
  if (isPublicDemoBuild) return (
    <main className="auth-screen">
      <section className="auth-panel panel">
        <h1>EcoKitchen demo unavailable</h1>
        <p role="alert">{notice || 'The read-only demo is currently unavailable.'}</p>
        <button className="button primary" type="button" onClick={() => window.location.reload()}>Reload demo</button>
      </section>
    </main>
  )
  return (
    <main className="auth-screen">
      <section className="auth-panel panel" aria-labelledby="auth-title">
        <span className="brand-icon"><Sprout size={24} aria-hidden="true" /></span>
        <h1 id="auth-title">Sign in to EcoKitchen</h1>
        <p>Access your restaurant operations dashboard.</p>
        <form onSubmit={signIn}>
          <label>Username<input name="username" required maxLength={64} autoComplete="username" disabled={busy} /></label>
          <label>Password<input name="password" type="password" required maxLength={256} autoComplete="current-password" disabled={busy} /></label>
          {notice && <p role="alert" className="auth-error">{notice}</p>}
          <button className="button primary" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </section>
    </main>
  )
}
