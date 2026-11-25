import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Archive, CalendarClock, CalendarDays, ClipboardList, Layers3, Package, Sprout } from 'lucide-react'
import { inventoryPath, queryPath, request, useResource } from './api'
import { formatDate, quantity } from './format'
import {
  AddButton, Badge, DataTable, EmptyState, HistoryTable, ItemName, LotTable, PageHeading, Pagination,
  PanelHeading, Resource, RowActions, SearchField, StatCard, alertGroups,
} from './ui'

const pageSize = 10

async function loadOverview(signal) {
  const [lots, ingredients, alerts, history, imported] = await Promise.all([
    request(queryPath(inventoryPath, { limit: 5 }), { signal }),
    request(queryPath(inventoryPath + '/ingredients', { limit: 1 }), { signal }),
    request(inventoryPath + '/alerts', { signal }),
    request(queryPath(inventoryPath + '/history', { limit: 4 }), { signal }),
    request(queryPath(inventoryPath, { limit: 1, source: 'csv_import' }), { signal }),
  ])
  return { lots, ingredients, alerts, history, imported }
}

function DatasetBanner({ count, total }) {
  const imported = count > 0
  const share = total > 0 ? count / total * 100 : 0
  return (
    <section className="dataset-banner" aria-label="Inventory data source">
      <div className="dataset-copy">
        <span className="dataset-label"><Sprout size={16} />{imported ? 'CSV data source' : 'SQLite data source'}</span>
        <h2>{imported ? 'Imported demo inventory' : 'Local inventory workspace'}</h2>
        <p>{imported ? 'Historical CSV lots retain their original expiry dates.' : 'Stock lots and transactions are stored in your local database.'}</p>
        <a className="button hero-button" href="#inventory">View inventory</a>
      </div>
      <div className="dataset-meter">
        <div className="meter-title"><span className="meter-icon"><Layers3 size={18} /></span><span>Inventory sources</span></div>
        <div className="meter-track" aria-hidden="true"><span style={{ width: share + '%' }} /></div>
        <div className="meter-legend">
          <span><i className="legend-dot imported-dot" />{count} imported</span>
          <span><i className="legend-dot manual-dot" />{total - count} manual</span>
        </div>
      </div>
    </section>
  )
}

export function Overview({ revision, openDialog }) {
  const resource = useResource(loadOverview, revision)
  const asOf = resource.data?.alerts.as_of
  return (
    <>
      <PageHeading title="Kitchen overview" description="Inventory, expiry and stock movements in one place.">
        {asOf && <span className="date-chip"><CalendarDays size={16} />{formatDate(asOf, true)}</span>}
        {openDialog && <AddButton onClick={() => openDialog({ type: 'lot' })}>Add stock lot</AddButton>}
      </PageHeading>
      <Resource resource={resource}>{({ lots, ingredients, alerts, history, imported }) => (
        <>
          <DatasetBanner count={imported.total} total={lots.total} />
          <section className="stat-grid" aria-label="Inventory indicators">
            <StatCard icon={Package} tone="green" label="Stock lots" value={lots.total}
              description={ingredients.total + (ingredients.total === 1 ? ' ingredient' : ' ingredients') + ' in the catalog'} />
            <StatCard icon={CalendarClock} tone="amber" label="Near expiry" value={alerts.near_expiry.length}
              description={'Expiring within ' + alerts.days + ' days'} />
            <StatCard icon={AlertTriangle} tone="red" label="Expired lots" value={alerts.expired.length}
              description="Past the recorded expiry date" />
            <StatCard icon={Archive} tone="blue" label="Low stock" value={alerts.low_stock.length}
              description="Ingredients below minimum stock" />
          </section>
          <div className="overview-grid">
            <section className="panel snapshot">
              <PanelHeading title="Inventory snapshot" subtitle="Stock lots from your inventory">
                <a className="text-link" href="#inventory">View all lots</a>
              </PanelHeading>
              {lots.items.length ? <LotTable items={lots.items} showSource={false} /> :
                <EmptyState title="No stock lots yet" description="Add an ingredient and a stock lot to begin tracking stock." />}
            </section>
            <section className="panel attention">
              <PanelHeading title="Needs attention" subtitle="Expiry and stock alerts" />
              <div className="attention-list">
                {alertGroups.map(({ key, label, icon: Icon, tone }) => (
                  <a className="attention-row" key={key} href={'#alerts?group=' + key}>
                    <span className={'alert-icon tone-' + tone}><Icon size={18} /></span>
                    <span>{label}</span><strong className={'count-pill tone-' + tone}>{alerts[key].length}</strong>
                  </a>
                ))}
              </div>
              <a className="attention-link text-link" href="#alerts">Review alerts</a>
            </section>
          </div>
          <section className="panel recent">
            <PanelHeading title="Recent stock transactions" subtitle="Latest changes recorded in the inventory">
              <a className="text-link" href="#history">View full history</a>
            </PanelHeading>
            {history.items.length ? <HistoryTable items={history.items} /> :
              <EmptyState title="No stock transactions" description="Stock changes will appear here." icon={ClipboardList} />}
          </section>
        </>
      )}</Resource>
    </>
  )
}

function IngredientTable({ items, onEdit, onArchive }) {
  return (
    <DataTable label="Ingredients" columns={['Ingredient', 'Stock unit', 'Minimum stock', 'Actions']}>
      {items.map(item => (
        <tr key={item.id}>
          <td><ItemName name={item.name} category={item.category} /></td>
          <td>{item.unit}</td>
          <td>{quantity(item.minimum_stock)} {item.unit}</td>
          <td><RowActions itemName={item.name} onEdit={onEdit && (() => onEdit(item))}
            onArchive={onArchive && (() => onArchive(item))} /></td>
        </tr>
      ))}
    </DataTable>
  )
}

function usePage(resource, page, setPage) {
  const lastPage = Math.max(1, Math.ceil((resource.data?.total ?? 0) / pageSize))
  const needsClamp = resource.data && page > lastPage
  useEffect(() => {
    if (needsClamp) setPage(lastPage)
  }, [needsClamp, lastPage, setPage])
  return !needsClamp
}

export function Inventory({ revision, openDialog }) {
  const [tab, setTab] = useState('lots')
  const [search, setSearch] = useState('')
  const [source, setSource] = useState('')
  const [page, setPage] = useState(1)
  const isLots = tab === 'lots'
  const load = useCallback(signal => request(queryPath(inventoryPath + (isLots ? '' : '/ingredients'), {
    limit: pageSize, offset: (page - 1) * pageSize, q: search.trim(), source: isLots ? source : '',
  }), { signal }), [isLots, page, search, source])
  const resource = useResource(load, revision)
  const visiblePage = usePage(resource, page, setPage)
  const noun = isLots ? 'lots' : 'ingredients'

  function chooseTab(value) {
    setTab(value)
    setSearch('')
    setSource('')
    setPage(1)
  }

  return (
    <>
      <PageHeading title="Inventory" description="Manage ingredient records, stock on hand and lot expiry dates.">
        {openDialog && <AddButton onClick={() => openDialog({ type: isLots ? 'lot' : 'ingredient' })}>
          {isLots ? 'Add stock lot' : 'Add ingredient'}
        </AddButton>}
      </PageHeading>
      <section className="panel inventory-panel">
        <div className="tabs" role="group" aria-label="Inventory views">
          <button type="button" className={isLots ? 'active' : ''} aria-pressed={isLots} onClick={() => chooseTab('lots')}>Stock lots</button>
          <button type="button" className={!isLots ? 'active' : ''} aria-pressed={!isLots} onClick={() => chooseTab('ingredients')}>Ingredients</button>
        </div>
        <div className="filter-row">
          <SearchField value={search} label={isLots ? 'Search stock lots' : 'Search ingredients'}
            placeholder="Search ingredient, category or unit…" onChange={value => { setSearch(value); setPage(1) }} />
          {isLots && <label className="select-label">
            <span>Source</span>
            <select value={source} onChange={event => { setSource(event.target.value); setPage(1) }}>
              <option value="">All sources</option><option value="csv_import">Imported demo</option><option value="manual">Manual</option>
            </select>
          </label>}
        </div>
        <Resource resource={resource}>{({ items, total }) => visiblePage && (
          items.length ? (
            <>
              {isLots ? <LotTable items={items} onEdit={openDialog && (item => openDialog({ type: 'lot', item }))}
                onArchive={openDialog && (item => openDialog({ type: 'archiveLot', item }))} /> :
                <IngredientTable items={items} onEdit={openDialog && (item => openDialog({ type: 'ingredient', item }))}
                  onArchive={openDialog && (item => openDialog({ type: 'archiveIngredient', item }))} />}
              <Pagination page={page} pageSize={pageSize} total={total} onChange={setPage} noun={noun} />
            </>
          ) : <EmptyState title={'No ' + noun + ' found'}
            description={search || source ? 'Clear the filters or try a different search.' : 'Add a record to start tracking inventory.'} />
        )}</Resource>
      </section>
    </>
  )
}

function LowStockTable({ items }) {
  return (
    <DataTable label="Low-stock ingredients" columns={['Ingredient', 'Available quantity', 'Minimum stock', 'Status']}>
      {items.map(item => (
        <tr key={item.id}>
          <td><ItemName name={item.name} category={item.category} /></td>
          <td className="nowrap">{quantity(item.available_quantity)} {item.unit}</td>
          <td className="nowrap">{quantity(item.minimum_stock)} {item.unit}</td>
          <td><Badge tone="amber">Below minimum</Badge></td>
        </tr>
      ))}
    </DataTable>
  )
}

const alertDescriptions = {
  near_expiry: 'Stock expiring today or within the selected window.',
  expired: 'Stock past its recorded expiry date.',
  low_stock: 'Ingredients with available stock below their recorded minimum.',
  unknown_expiry: 'Stock without a verified expiry date.',
}

export function Alerts({ revision, params, openDialog }) {
  const [days, setDays] = useState(7)
  const [page, setPage] = useState(1)
  const requested = params.get('group')
  const group = alertGroups.find(item => item.key === requested) ?? alertGroups[0]
  const load = useCallback(signal => request(queryPath(inventoryPath + '/alerts', { days }), { signal }), [days])
  const resource = useResource(load, revision)
  const total = resource.data?.[group.key].length ?? 0
  const visiblePage = usePage({ data: resource.data && { total } }, page, setPage)

  return (
    <>
      <PageHeading title="Inventory alerts" description="Review expiry dates and ingredients below their minimum stock.">
        <label className="select-label window-selector"><span>Expiry window</span>
          <select value={days} onChange={event => { setDays(Number(event.target.value)); setPage(1) }}>
            <option value="0">Today</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option>
          </select>
        </label>
      </PageHeading>
      <Resource resource={resource}>{data => (
        <>
          <nav className="alert-tabs" aria-label="Alert categories">
            {alertGroups.map(({ key, label, icon: Icon, tone }) => (
              <a key={key} className={'alert-tab ' + (key === group.key ? 'active' : '')}
                href={'#alerts?group=' + key} aria-current={key === group.key ? 'true' : undefined}
                onClick={() => setPage(1)}>
                <span className={'alert-icon tone-' + tone}><Icon size={19} /></span>
                <span>{label}</span><strong>{data[key].length}</strong>
              </a>
            ))}
          </nav>
          <section className="panel">
            <PanelHeading title={group.label} subtitle={alertDescriptions[group.key]} />
            {visiblePage && (total ? (
              <>
                {group.key === 'low_stock' ?
                  <LowStockTable items={data.low_stock.slice((page - 1) * pageSize, page * pageSize)} /> :
                  <LotTable items={data[group.key].slice((page - 1) * pageSize, page * pageSize)}
                    onEdit={openDialog && (item => openDialog({ type: 'lot', item }))} />}
                <Pagination page={page} pageSize={pageSize} total={total} onChange={setPage}
                  noun={group.key === 'low_stock' ? 'ingredients' : 'lots'} />
              </>
            ) : <EmptyState title="No alerts in this category" icon={group.icon} />)}
          </section>
        </>
      )}</Resource>
    </>
  )
}

export function History({ revision }) {
  const [page, setPage] = useState(1)
  const [lotId, setLotId] = useState('')
  const [filter, setFilter] = useState('')
  const load = useCallback(signal => request(queryPath(inventoryPath + '/history', {
    limit: pageSize, offset: (page - 1) * pageSize, lot_id: filter,
  }), { signal }), [page, filter])
  const resource = useResource(load, revision)
  const visiblePage = usePage(resource, page, setPage)

  function applyFilter(event) {
    event.preventDefault()
    setPage(1)
    setFilter(lotId.trim())
  }

  return (
    <>
      <PageHeading title="Stock history" description="Review lot imports, adjustments and archived stock." />
      <section className="panel">
        <PanelHeading title="Stock transactions" subtitle="Newest transactions first" />
        <form className="filter-row" onSubmit={applyFilter}>
          <label className="select-label"><span>Lot ID</span>
            <input type="text" inputMode="numeric" pattern="[0-9]+" maxLength={19} placeholder="All lots"
              value={lotId} onChange={event => setLotId(event.target.value)} />
          </label>
          <button type="submit" className="button secondary">Apply filter</button>
          {filter && <button type="button" className="button quiet" onClick={() => { setLotId(''); setFilter(''); setPage(1) }}>Clear filter</button>}
        </form>
        <Resource resource={resource} label="Loading stock history">{({ items, total }) => visiblePage && (
          items.length ? (
            <>
              <HistoryTable items={items} />
              <Pagination page={page} pageSize={pageSize} total={total} onChange={setPage} noun="transactions" />
            </>
          ) : <EmptyState title="No transactions found" description="Recorded stock changes will appear here." icon={ClipboardList} />
        )}</Resource>
      </section>
    </>
  )
}
