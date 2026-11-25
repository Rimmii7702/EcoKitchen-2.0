import { useEffect, useRef, useState } from 'react'
import {
  Bell, CalendarDays, ChefHat, ChevronRight, CircleCheck, ClipboardList, ExternalLink, House, Leaf,
  Lightbulb, LineChart, LogOut, Map, Menu, Package, RefreshCw, ScanLine, Sprout, X,
} from 'lucide-react'
import { apiBase } from './api'
import { InventoryDialog } from './forms'
import { Alerts, History, Inventory, Overview } from './views'
import { VisionPage } from './vision'
import { HeatmapPage, RecipePage, SpecialsPage, WastePage } from './ai_pages'
import { ForecastingPage } from './forecasting'
import { RecommendationsPage } from './recommendations'
import { SessionGate } from './auth'

const workspace = [
  { id: 'overview', label: 'Overview', icon: House, view: Overview },
  { id: 'inventory', label: 'Inventory', icon: Package, view: Inventory },
  { id: 'alerts', label: 'Alerts', icon: Bell, view: Alerts },
  { id: 'recommendations', label: 'Smart Recommendations', icon: Lightbulb, view: RecommendationsPage },
  { id: 'history', label: 'Stock history', icon: ClipboardList, view: History },
  { id: 'detection', label: 'Spoilage checks', icon: ScanLine, view: VisionPage },
  { id: 'recipes', label: 'Recipes', icon: ChefHat, view: RecipePage },
  { id: 'specials', label: "Today's Specials", icon: CalendarDays, view: SpecialsPage },
  { id: 'waste', label: 'Food-waste analytics', icon: Leaf, view: WastePage },
  { id: 'heatmap', label: 'Kitchen heatmap', icon: Map, view: HeatmapPage },
  { id: 'forecasting', label: 'Forecasting', icon: LineChart, view: ForecastingPage },
]

function fromHash() {
  const [id, query] = window.location.hash.slice(1).split('?')
  return { item: workspace.find(item => item.id === id) ?? workspace[0], params: new URLSearchParams(query) }
}

function NavLinks({ items, active, onNavigate }) {
  return items.map(({ id, icon: Icon, label }) => (
    <a key={id} href={'#' + id} className={'nav-link ' + (active === id ? 'selected' : '')}
      aria-current={active === id ? 'page' : undefined} onClick={onNavigate}>
      <Icon size={19} strokeWidth={1.8} />{label}
    </a>
  ))
}

function Sidebar({ active, isOpen, onClose, user }) {
  return (
    <>
      {isOpen && <button type="button" className="nav-scrim" onClick={onClose} aria-label="Dismiss navigation" />}
      <aside id="dashboard-sidebar" className={'sidebar ' + (isOpen ? 'is-open' : '')}>
        <a className="brand" href="#overview" onClick={onClose}>
          <span className="brand-icon"><Sprout size={24} strokeWidth={2} /></span>
          <span><strong>EcoKitchen</strong><small>Restaurant operations</small></span>
        </a>
        <button type="button" className="icon-button mobile-close quiet" onClick={onClose} aria-label="Close menu"><X size={21} /></button>
        <nav aria-label="Dashboard navigation">
          <p className="nav-label">Workspace</p>
          <NavLinks items={workspace} active={active} onNavigate={onClose} />
        </nav>
        <div className="sidebar-footer">
          <p className="auth-user"><strong>{user.username}</strong> · {user.role === 'admin' ? 'Administrator' : 'Viewer'}</p>
          <a className="nav-link legacy-link" href={apiBase + '/legacy/'} target="_blank" rel="noreferrer">
            <ExternalLink size={17} />Open Flask UI
          </a>
        </div>
      </aside>
    </>
  )
}

function TopBar({ active, isOpen, toggleNav, onRefresh, user, signOut, signingOut }) {
  return (
    <header className="topbar">
      <div className="topbar-start">
        <button type="button" className="icon-button menu-toggle quiet" aria-label="Toggle dashboard navigation"
          aria-controls="dashboard-sidebar" aria-expanded={isOpen} onClick={toggleNav}><Menu size={23} /></button>
        <div className="breadcrumb"><span>Workspace</span><ChevronRight size={14} aria-hidden="true" />
          <strong>{active.label}</strong></div>
      </div>
      <div className="topbar-actions">
        <span className="local-chip"><i />Local development</span>
        <span className="auth-user"><strong>{user.username}</strong> · {user.role === 'admin' ? 'Administrator' : 'Viewer · read only'}</span>
        <button type="button" className="icon-button refresh-button" onClick={onRefresh}
          aria-label="Refresh dashboard" title="Refresh dashboard"><RefreshCw size={17} /></button>
        <button type="button" className="icon-button" onClick={signOut} disabled={signingOut}
          aria-label="Sign out" title="Sign out"><LogOut size={17} /></button>
      </div>
    </header>
  )
}

export function Dashboard({ user, signOut, authError, signingOut }) {
  const [route, setRoute] = useState(fromHash)
  const [isOpen, setIsOpen] = useState(false)
  const [revision, setRevision] = useState(0)
  const [dialog, setDialog] = useState(null)
  const [notice, setNotice] = useState('')
  const mainRef = useRef(null)
  const active = route.item
  const View = active.view
  const canManage = user.role === 'admin'

  useEffect(() => {
    const onHashChange = () => {
      setRoute(fromHash())
      setIsOpen(false)
      setDialog(null)
      mainRef.current?.focus()
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    document.title = active.label + ' · EcoKitchen'
  }, [active])

  useEffect(() => {
    if (!notice) return undefined
    const timeout = setTimeout(() => setNotice(''), 7000)
    return () => clearTimeout(timeout)
  }, [notice])

  const refresh = () => setRevision(value => value + 1)

  function onSaved(message) {
    setDialog(null)
    setNotice(message)
    refresh()
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#dashboard-main" onClick={event => {
        event.preventDefault()
        mainRef.current?.focus()
      }}>Skip to dashboard content</a>
      <Sidebar user={user} active={active.id} isOpen={isOpen} onClose={() => setIsOpen(false)} />
      <div className="workspace">
        <TopBar user={user} signOut={signOut} signingOut={signingOut}
          active={active} isOpen={isOpen} toggleNav={() => setIsOpen(value => !value)} onRefresh={refresh} />
        <main ref={mainRef} id="dashboard-main" className="main-content" tabIndex="-1">
          {authError && <p role="alert" className="auth-notice">{authError}</p>}
          {!canManage && <p className="auth-notice">Viewer access is read only. Administrators can change stock or run analysis.</p>}
          <View revision={revision} params={route.params} openDialog={canManage ? setDialog : undefined} canManage={canManage} />
        </main>
      </div>
      {canManage && dialog && <InventoryDialog key={dialog.type + (dialog.item?.id ?? '')} spec={dialog}
        onClose={() => setDialog(null)} onSave={onSaved} openDialog={setDialog} />}
      {notice && <div className="toast" role="status"><CircleCheck size={20} /><span>{notice}</span>
        <button type="button" className="icon-button quiet" aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={17} /></button>
      </div>}
    </div>
  )
}

export default function App() {
  return <SessionGate>{session => <Dashboard {...session} />}</SessionGate>
}
