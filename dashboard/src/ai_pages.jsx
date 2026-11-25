import { useState } from 'react'
import { AlertCircle, CalendarDays, ChefHat, Clock3, Image as ImageIcon, Leaf, Map, Package, Sparkles, UploadCloud } from 'lucide-react'
import { request, usePostAction, useResource } from './api'
import { formatDate, formatTime, quantity } from './format'
import { Badge, DataTable, EmptyState, Loading, PageHeading, PanelHeading, SearchField } from './ui'
import { mediaUrl, MediaPreview, usePreview } from './vision'
import './ai.css'

const aiPath = '/api/ai/'

export function useAiAction() {
  const action = usePostAction({ service: 'AI', timeoutMs: 180000, keepResult: true })
  return { ...action, run: (endpoint, body) => action.run(aiPath + endpoint, body) }
}

export function AiError({ error, retry }) {
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={20} />
      <div><strong>{error.status === 503 ? 'Feature unavailable' : 'Could not complete the request'}</strong>
        <p>{error.message ?? error}</p></div>
      {retry && <button type="button" className="button secondary" onClick={retry}>Try again</button>}
    </div>
  )
}

function SourceBadges({ item }) {
  return (
    <span className="ai-tags">
      {item.sources.includes('csv_import') && <Badge tone="violet">Imported demo</Badge>}
      {item.sources.includes('manual') && <Badge>Manual stock</Badge>}
      {item.near_expiry && <Badge tone="amber">Near expiry</Badge>}
    </span>
  )
}

function IngredientList({ items, selected, limit, locked, onChange }) {
  return (
    <fieldset className="ai-ingredient-list" disabled={locked}>
      <legend className="sr-only">Select ingredients from SQLite</legend>
      {items.map(item => (
        <label className="ai-ingredient-option" key={item.id}>
          <input type="checkbox" checked={selected.includes(item.id)}
            disabled={selected.length >= limit && !selected.includes(item.id)} onChange={event => onChange(item.id, event.target.checked)} />
          <span className="ai-ingredient-info">
            <strong>{item.name}</strong>
            <small>{quantity(item.available_quantity)} {item.unit} available
              {item.earliest_expiry && ' · Earliest expiry ' + formatDate(item.earliest_expiry)}</small>
            <SourceBadges item={item} />
          </span>
        </label>
      ))}
    </fieldset>
  )
}

export function RecipeResult({ data }) {
  const { recipe, inventory_inputs: inputs } = data
  return (
    <article className="ai-result-body">
      <Badge tone="violet">Gemini suggestion</Badge>
      <h3 className="ai-result-title">{recipe.name}</h3>
      {(recipe.estimated_cooking_time || recipe.servings) && <div className="ai-tags">
        {recipe.estimated_cooking_time && <Badge><Clock3 size={14} /> {recipe.estimated_cooking_time}</Badge>}
        {recipe.servings && <Badge>Servings: {recipe.servings}</Badge>}
      </div>}
      <section className="ai-recipe-block ai-recipe-inputs">
        <h4>SQLite ingredients sent</h4>
        <ul>{inputs.map(item => <li key={item.id}>{item.name} · {quantity(item.available_quantity)} {item.unit} available</li>)}</ul>
      </section>
      <section className="ai-recipe-block"><h4>Suggested ingredients</h4><ul>{recipe.ingredients.map((ingredient, index) => <li key={index}>{ingredient}</li>)}</ul></section>
      <section className="ai-recipe-block"><h4>Preparation steps</h4><ol>{recipe.steps.map((step, index) => <li key={index}>{step}</li>)}</ol></section>
      {recipe.waste_reduction_tips?.length > 0 && <section className="ai-recipe-block">
        <h4>Waste-reduction suggestions</h4><ul>{recipe.waste_reduction_tips.map((tip, index) => <li key={index}>{tip}</li>)}</ul>
      </section>}
    </article>
  )
}

const loadRecipeInputs = signal => request(aiPath + 'recipe-ingredients', { signal, service: 'AI' })

export function RecipePage({ revision, canManage }) {
  const resource = useResource(loadRecipeInputs, revision)
  const action = useAiAction()
  const [selected, setSelected] = useState([])
  const [search, setSearch] = useState('')
  const items = resource.data?.items ?? []
  const limit = resource.data?.selection_limit
  const near = items.filter(item => item.near_expiry)
  const visible = items.filter(item => item.name.toLowerCase().includes(search.trim().toLowerCase()))

  return (
    <>
      <PageHeading title="Recipe recommendations" description="Choose available stock to give Gemini a starting point for a recipe." />
      <div className="ai-workspace">
        <section className="panel">
          <PanelHeading title="Available from SQLite" subtitle={resource.data && 'Near expiry includes the next ' + resource.data.near_expiry_days + ' days.'} />
          {resource.loading ? <Loading label="Loading available ingredients" /> : resource.error ?
            <AiError error={resource.error} retry={resource.retry} /> : !items.length ?
              <EmptyState title="No available ingredients" description="Add available lots in Inventory to generate a recipe." icon={Package} /> :
              <form onSubmit={event => { event.preventDefault(); if (canManage) action.run('recipes', { ingredient_ids: selected }) }}>
                <div className="ai-filter">
                  <SearchField value={search} label="Search available ingredients" placeholder="Search by ingredient…" onChange={setSearch} />
                  <button type="button" className="button secondary" disabled={!canManage || !near.length || action.busy}
                    onClick={() => setSelected(near.slice(0, limit).map(item => item.id))}>Select near expiry</button>
                </div>
                {visible.length ? <IngredientList items={visible} selected={selected} locked={!canManage || action.busy} limit={limit}
                  onChange={(id, checked) => setSelected(previous => checked ? [...previous, id] : previous.filter(value => value !== id))} /> :
                  <EmptyState title="No matching ingredients" />}
                <div className="ai-controls">
                  <p aria-live="polite">{selected.length} of {limit} selected</p>
                  <button type="submit" className="button primary" disabled={!canManage || !selected.length || action.busy}>
                    <Sparkles size={17} />{action.busy ? 'Generating…' : 'Generate recipe'}</button>
                </div>
              </form>}
        </section>
        <section className="panel" aria-busy={action.busy}>
          <PanelHeading title="Recipe suggestion" />
          {action.error && <AiError error={action.error} />}
          {action.busy ? <Loading label="Generating a recipe with Gemini" /> : action.data ?
            <RecipeResult data={action.data} /> : !action.error &&
              <EmptyState title="Choose ingredients to begin" description="A Gemini recipe will appear after you select Generate recipe." icon={ChefHat} />}
        </section>
      </div>
    </>
  )
}

const loadMenu = signal => request(aiPath + 'menu', { signal, service: 'AI' })

function MenuCard({ dish }) {
  return (
    <article className="ai-menu-card">
      <Badge tone="violet">Gemini suggestion</Badge>
      <h3>{dish.name}</h3>
      {dish.description && <p>{dish.description}</p>}
      {dish.ingredients?.length > 0 && <div className="ai-tags">
        {dish.ingredients.map((ingredient, index) => <Badge tone="green" key={index}>{ingredient}</Badge>)}
      </div>}
      {dish.recipe && <details><summary>View preparation</summary><p className="ai-menu-recipe">{dish.recipe}</p></details>}
    </article>
  )
}

export function SpecialsPage({ revision, canManage }) {
  const action = useAiAction()
  const resource = useResource(loadMenu, revision + ':' + (action.data?.latest_snapshot?.id ?? ''))
  const [choice, setChoice] = useState('latest')
  const [group, setGroup] = useState('todays_special')
  const saved = resource.data?.saved_snapshot
  const latest = resource.data?.latest_snapshot ?? action.data?.latest_snapshot
  const previous = resource.data?.previous_snapshots ?? []
  const selectedSnapshot = choice === 'saved' ? saved : choice === 'latest' ? latest : previous.find(item => item.id === choice)
  const snapshot = selectedSnapshot ?? saved ?? latest
  const snapshotChoice = snapshot === saved ? 'saved' : snapshot === latest ? 'latest' : snapshot?.id
  const dishes = Object.values(snapshot?.[group] ?? {})

  return (
    <>
      <PageHeading title="Today's Specials" description="Browse saved specials and two-week recommendations, or generate a fresh menu from SQLite stock.">
        {canManage && <button type="button" className="button primary" disabled={action.busy}
          onClick={async () => { if (await action.run('menu')) setChoice('latest') }}>
          <Sparkles size={17} />{action.busy ? 'Generating…' : 'Generate fresh menu'}
        </button>}
      </PageHeading>
      {resource.error && <AiError error={resource.error} retry={resource.retry} />}
      {action.error && <AiError error={action.error} />}
      {action.busy && <Loading label="Generating specials and the two-week menu with Gemini" />}
      {resource.loading && !snapshot ? <Loading label="Reading saved menus" /> : snapshot ? (
        <>
          <section className="panel ai-snapshot-bar">
            <div>
              <h2>{snapshot.fictional_demo ? 'Fictional demo menu' : snapshot === saved ? 'Saved menu snapshot' : 'Dashboard snapshot'}</h2>
              <p><CalendarDays size={16} />{snapshot.date ? 'Generated ' + formatDate(snapshot.date, true) : 'Generation date unavailable'}
                {snapshot.generated_at && ' · ' + formatTime(snapshot.generated_at)}</p>
            </div>
            {latest && (saved || previous.length > 0) && <label className="select-label"><span>Snapshot</span>
              <select value={snapshotChoice} onChange={event => setChoice(event.target.value)}>
                <option value="latest">Latest dashboard snapshot</option>
                {previous.map(item => <option key={item.id} value={item.id}>
                  {formatDate(item.date)}{item.generated_at && ' · ' + formatTime(item.generated_at)}</option>)}
                {saved && <option value="saved">Original saved snapshot</option>}
              </select>
            </label>}
          </section>
          <div className="vision-tabs" role="group" aria-label="Menu period">
            <button type="button" className={'vision-tab ' + (group === 'todays_special' ? 'selected' : '')}
              aria-pressed={group === 'todays_special'} onClick={() => setGroup('todays_special')}>Specials</button>
            <button type="button" className={'vision-tab ' + (group === 'two_week_menu' ? 'selected' : '')}
              aria-pressed={group === 'two_week_menu'} onClick={() => setGroup('two_week_menu')}>Two-week menu</button>
          </div>
          {dishes.length ? <div className="ai-menu-grid">{dishes.map((dish, index) => <MenuCard key={index} dish={dish} />)}</div> :
            <section className="panel"><EmptyState title="No dishes in this snapshot for the selected period" icon={ChefHat} /></section>}
        </>
      ) : !resource.error && !action.busy && <section className="panel"><EmptyState title="No saved menu snapshot" icon={ChefHat} /></section>}
    </>
  )
}

function OutputImage({ label, path }) {
  const url = mediaUrl(path)
  return url && <figure className="vision-result-media">
    <MediaPreview key={url} src={url} alt={label} stored /><figcaption>{label}</figcaption>
  </figure>
}

function HeatmapResult({ data }) {
  return (
    <>
      <div className="ai-result-body">
        <Badge tone="amber">Simulated waste values</Badge>
        <div className="ai-estimate"><strong>{quantity(data.simulated_total_kg)} kg</strong><span>Simulated total from station waste levels</span></div>
      </div>
      <div className="ai-media-grid">
        <OutputImage path={data.heatmap_url} label="Simulated kitchen heatmap" />
        <OutputImage path={data.distribution_url} label="Simulated waste distribution" />
      </div>
      <DataTable label="Simulated kitchen station waste" columns={['Station', 'Gemini level', 'Simulated weight']}>
        {data.stations.map((station, index) => (
          <tr key={index}><td>{station.area}</td>
            <td><Badge tone={station.waste_level === 'High' ? 'red' : station.waste_level === 'Medium' ? 'amber' : 'green'}>{station.waste_level}</Badge></td>
            <td>{quantity(station.waste_kg)} kg</td>
          </tr>
        ))}
      </DataTable>
    </>
  )
}

function PhotoAnalysis({ endpoint, title, description, actionLabel, resultTitle, Result, Icon }) {
  const action = useAiAction()
  const [file, setFile] = useState(null)
  const [fileError, setFileError] = useState('')
  const preview = usePreview(file)

  function choose(selected) {
    setFile(null)
    setFileError('')
    action.reset()
    if (!selected) return
    if (!/\.(jpe?g|png|bmp)$/i.test(selected.name)) setFileError('Choose a JPEG, PNG, or BMP image.')
    else if (!selected.size || selected.size > 10 * 1024 ** 2) setFileError('Choose a nonempty image up to 10 MiB.')
    else setFile(selected)
  }

  function submit(event) {
    event.preventDefault()
    if (!file) return
    const body = new FormData()
    body.append('image', file)
    action.run(endpoint, body)
  }

  return (
    <>
      <PageHeading title={title} description={description} />
      <div className="ai-workspace">
        <section className="panel">
          <PanelHeading title="Upload a photo" subtitle="Gemini receives the photo when you submit it." />
          <form className="vision-form" onSubmit={submit}>
            <label className="vision-drop">
              <input type="file" className="sr-only" accept=".jpg,.jpeg,.png,.bmp" disabled={action.busy}
                onChange={event => choose(event.target.files[0])} />
              <span className="vision-drop-icon"><UploadCloud size={23} /></span>
              <strong>Choose an image</strong><span>JPG, JPEG, PNG, BMP · Up to 10 MiB</span>
            </label>
            {fileError && <AiError error={fileError} />}
            {file && <div className="vision-file"><span><strong>{file.name}</strong></span></div>}
            {preview && <figure className="vision-local-preview">
              <figcaption>Image preview</figcaption><MediaPreview key={preview} src={preview} alt="Selected image preview" />
            </figure>}
            <button type="submit" className="button primary vision-submit" disabled={!file || action.busy}>
              <Icon size={17} />{action.busy ? 'Analyzing…' : actionLabel}
            </button>
          </form>
        </section>
        <section className="panel" aria-busy={action.busy}>
          <PanelHeading title={resultTitle} />
          {action.error && <AiError error={action.error} />}
          {action.busy ? <Loading label="Analyzing the uploaded photo" /> : action.data && !action.error ?
            <Result data={action.data} /> : !action.error &&
            <EmptyState title="Ready for a photo" description="The analysis appears after you submit an image." icon={ImageIcon} />}
        </section>
      </div>
    </>
  )
}

export function WastePage() {
  return <PageHeading title="Food-waste analytics" description="Annotated waste-photo analysis is unavailable in this edition. The Forecasting page includes the separate synthetic waste experiment." />
}

export function HeatmapPage({ canManage }) {
  if (!canManage) return <PageHeading title="Kitchen heatmap" description="Photo analysis requires an administrator." />
  return <PhotoAnalysis endpoint="heatmap" title="Kitchen heatmap" description="Map simulated waste levels across kitchen stations in a photo."
    actionLabel="Generate heatmap" resultTitle="Simulated kitchen analysis" Result={HeatmapResult} Icon={Map} />
}
