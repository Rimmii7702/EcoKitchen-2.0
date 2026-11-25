import { useEffect, useRef, useState } from 'react'
import { CalendarClock, CircleHelp, PackageMinus, Sparkles, TriangleAlert } from 'lucide-react'
import { request, useResource } from './api'
import { AiError, RecipeResult, useAiAction } from './ai_pages'
import { formatDate, quantity } from './format'
import { Badge, DataTable, EmptyState, Loading, PageHeading, Pagination, PanelHeading, StatCard } from './ui'
import './recommendations.css'

const path = '/api/recommendations'
const loadRecommendations = signal => request(path, { signal, service: 'recommendations' })
const pageSize = 8
const formatCost = new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 })

function Source({ lot }) {
  return lot.source === 'csv_import' ? <Badge tone="violet">Imported demo</Badge> : <Badge>Manual</Badge>
}

function PagedRows({ items, label, columns, noun, empty, renderRow }) {
  const [page, setPage] = useState(1)
  if (!items.length) return <EmptyState title={empty} />
  const offset = (page - 1) * pageSize
  return <>
    <DataTable label={label} columns={columns}>{items.slice(offset, offset + pageSize).map(renderRow)}</DataTable>
    <Pagination page={page} pageSize={pageSize} total={items.length} onChange={setPage} noun={noun} />
  </>
}

function ExpiryPanel({ title, subtitle, lots, empty, action }) {
  return <section className="panel">
    <PanelHeading title={title} subtitle={subtitle} />
    <PagedRows items={lots} label={title + ' lots'} columns={['Ingredient and lot', 'Quantity', 'Expiry', 'Source', 'Recommended action']}
      noun="lots" empty={empty} renderRow={lot => <tr key={lot.id}>
        <td><strong>{lot.ingredient.name}</strong><small className="cell-note">Lot #{lot.id}</small></td>
        <td>{quantity(lot.quantity)} {lot.ingredient.unit}</td>
        <td>{lot.expiry_date ? formatDate(lot.expiry_date) : 'Unknown'}
          {lot.source_expiry_text && !lot.expiry_date && <small className="cell-note">Imported value {lot.source_expiry_text}</small>}</td>
        <td><Source lot={lot} /></td>
        <td>{action}</td>
      </tr>} />
  </section>
}

const costReasons = {
  no_eligible_manual_lots: 'No eligible manual lots with cost evidence',
  missing_unit_costs: 'Eligible manual lots have missing or zero unit costs',
  conflicting_unit_costs: 'Eligible manual lots have conflicting unit costs',
}

function Cost({ cost, replenishment, unit }) {
  if (Number(replenishment) === 0) return <span>No replenishment needed</span>
  return <>
    {cost.estimated_cost === null ? <>
      <strong>Cost unavailable</strong>
      <small className="cell-note">{costReasons[cost.basis]}</small>
    </> : <>
      <strong>{formatCost.format(cost.estimated_cost)} in {cost.basis === 'simulation_assumption' ? 'assumed' : 'recorded'} cost units</strong>
      <small className="cell-note">{formatCost.format(cost.unit_cost)} per {unit} · {
        cost.basis === 'simulation_assumption' ? 'Simulated unit cost' : 'Recorded manual unit cost'}</small>
    </>}
    {cost.evidence.length > 0 && <details className="recommendation-evidence">
      <summary>Cost records</summary>
      <ul>{cost.evidence.map(lot => <li key={lot.lot_id}>Lot #{lot.lot_id} · {
        lot.unit_cost === null ? 'Missing cost' : formatCost.format(lot.unit_cost) + ' per ' + unit}</li>)}</ul>
    </details>}
  </>
}

function Replenishment({ items }) {
  return <section className="panel">
    <PanelHeading title="Replenishment" subtitle="Rule-based quantity equals the minimum stock level minus eligible stock when there is a shortfall." />
    <PagedRows items={items} label="Rule-based replenishment" noun="ingredients" empty="No ingredients below minimum stock"
      columns={['Ingredient', 'Eligible stock', 'Minimum stock', 'Review purchase of', 'Estimated purchasing cost']}
      renderRow={item => <tr key={item.id}>
        <td><strong>{item.name}</strong><small className="cell-note">{item.category}</small></td>
        <td>{quantity(item.eligible_quantity)} {item.unit}
          {item.eligible_lots.length > 0 ? <details className="recommendation-evidence">
            <summary>Stock records</summary>
            <ul>{item.eligible_lots.map(lot => <li key={lot.id}>
              Lot #{lot.id} · {quantity(lot.quantity)} {item.unit} · Expires {formatDate(lot.expiry_date)} <Source lot={lot} />
            </li>)}</ul>
          </details> : <small className="cell-note">No eligible lots</small>}
        </td>
        <td>{quantity(item.minimum_stock)} {item.unit}</td>
        <td><strong>{quantity(item.replenishment_quantity)} {item.unit}</strong></td>
        <td><Cost cost={item.cost} replenishment={item.replenishment_quantity} unit={item.unit} /></td>
      </tr>} />
  </section>
}

function PriorityRecipe({ lots, ids }) {
  const action = useAiAction()
  const candidates = ids.map(id => lots.find(lot => lot.ingredient_id === id))
  return <section className="panel" aria-busy={action.busy}>
    <PanelHeading title="Cook with near-expiry stock" subtitle="Request a Gemini suggestion using SQLite ingredients prioritized by earliest expiry." />
    {candidates.length ? <div className="recommendation-body">
      <p>These ingredients are proposed from the priority lots below. Gemini may suggest additional ingredients.</p>
      <details className="recommendation-evidence" open>
        <summary>Priority recipe inputs ({candidates.length})</summary>
        <ul>{candidates.map(lot => <li key={lot.id}>{lot.ingredient.name} · Lot #{lot.id} · {
          formatDate(lot.expiry_date)} <Source lot={lot} /></li>)}</ul>
      </details>
      <button type="button" className="button secondary" disabled={action.busy}
        onClick={() => {
          action.reset()
          action.run('recipes', { ingredient_ids: ids, near_expiry_only: true })
        }}>
        <Sparkles size={17} />{action.busy ? 'Generating…' : 'Generate priority recipe'}
      </button>
    </div> : <EmptyState title="No near-expiry ingredients for a priority recipe" />}
    {action.error && <AiError error={action.error} />}
    {action.busy && <Loading label="Generating a recipe with Gemini" />}
    {action.data && <RecipeResult data={action.data} />}
  </section>
}

function Scenario({ ingredients }) {
  const active = useRef(null)
  const [ingredientId, setIngredientId] = useState('')
  const [state, setState] = useState({})
  const ingredient = ingredients.find(item => String(item.id) === ingredientId)
  useEffect(() => () => active.current?.abort(), [])

  async function submit(event) {
    event.preventDefault()
    if (!ingredient || active.current) return
    const form = new FormData(event.currentTarget)
    const assumed = form.get('assumed_unit_cost').trim()
    const controller = new AbortController()
    active.current = controller
    setState({ busy: true })
    try {
      const result = await request(path + '/simulate', {
        method: 'POST', signal: controller.signal, service: 'recommendations',
        body: {
          ingredient_id: ingredient.id, consume_quantity: form.get('consume_quantity'),
          ...(assumed !== '' && { assumed_unit_cost: assumed }),
        },
      })
      if (!controller.signal.aborted) setState({ result })
    } catch (error) {
      if (!controller.signal.aborted) setState({ error: error.message })
    } finally {
      active.current = null
    }
  }

  const { busy, error, result } = state
  return <section className="panel" aria-busy={busy}>
    <PanelHeading title="Consumption and cost simulation" subtitle="Preview first-expiring, first-out allocations and the resulting replenishment quantity. Inventory stays unchanged." />
    {!ingredients.length ? <EmptyState title="Add ingredients to run a simulation" /> : <>
      <form className="recommendation-body" onSubmit={submit} onChange={() => setState({})}>
        <fieldset className="recommendation-fields" disabled={busy}>
          <legend className="sr-only">Simulation inputs</legend>
          <label className="form-field">Ingredient
            <select value={ingredientId} required onChange={event => setIngredientId(event.target.value)}>
              <option value="">Choose an ingredient</option>
              {ingredients.map(item => <option key={item.id} value={item.id}>{item.name} · {item.category} · {item.unit}</option>)}
            </select>
          </label>
          {ingredient && <p className="form-context">{quantity(ingredient.eligible_quantity)} {ingredient.unit} eligible in SQLite</p>}
          <div className="form-grid">
            <label className="form-field">Hypothetical consumption{ingredient ? ' (' + ingredient.unit + ')' : ''}
              <input name="consume_quantity" type="number" required min="0" max="999999999.999" step="0.001" defaultValue="0" />
            </label>
            <label className="form-field">Assumed unit cost (optional)
              <input name="assumed_unit_cost" type="number" min="0" max="999999999.999" step="0.001" />
              <small className="form-hint">Leave blank to use eligible recorded costs.</small>
            </label>
          </div>
          <button type="submit" className="button primary" disabled={!ingredient}>Run simulation</button>
        </fieldset>
      </form>
      {busy && <Loading label="Calculating simulation" />}
      {error && <p className="form-error recommendation-body" role="alert">{error}</p>}
      {result && <ScenarioResult result={result} />}
    </>}
  </section>
}

function ScenarioResult({ result }) {
  const unit = result.ingredient.unit
  return <div className="recommendation-body recommendation-result" aria-live="polite">
    <Badge tone="violet">Simulation</Badge>
    <h3>Projected stock and replenishment</h3>
    <dl>
      <div><dt>Starting eligible stock</dt><dd>{quantity(result.eligible_quantity)} {unit}</dd></div>
      <div><dt>Requested consumption</dt><dd>{quantity(result.requested_consumption)} {unit}</dd></div>
      <div><dt>Projected eligible stock</dt><dd>{quantity(result.projected_quantity)} {unit}</dd></div>
      <div><dt>Unfulfilled consumption</dt><dd>{quantity(result.unfulfilled_quantity)} {unit}</dd></div>
      <div><dt>Minimum stock</dt><dd>{quantity(result.ingredient.minimum_stock)} {unit}</dd></div>
      <div><dt>Projected replenishment</dt><dd>{quantity(result.replenishment_quantity)} {unit}</dd></div>
      <div><dt>Projected purchasing cost</dt><dd><Cost cost={result.cost} replenishment={result.replenishment_quantity} unit={unit} /></dd></div>
    </dl>
    {result.allocations.length > 0 && <ol className="recommendation-allocations" aria-label="Simulated FEFO allocations">
      {result.allocations.map(lot => <li key={lot.id}>Lot #{lot.id} · {quantity(lot.allocated_quantity)} {unit} · Expires {
        formatDate(lot.expiry_date)} <Source lot={lot} /></li>)}
    </ol>}
  </div>
}

export function RecommendationsPage({ revision, canManage }) {
  const resource = useResource(loadRecommendations, revision)
  const data = resource.data
  return <>
    <PageHeading title="Smart Recommendations" description="Prioritize expiring stock, review purchasing quantities, and explore hypothetical stock changes." />
    {resource.loading ? <Loading label="Loading recommendations" /> : resource.error ?
      <AiError error={resource.error} retry={resource.retry} /> : <>
        <div className="stat-grid">
          <StatCard icon={CalendarClock} label="Near-expiry lots" value={data.near_expiry.length} tone="amber" description={'Due within ' + data.near_expiry_days + ' days'} />
          <StatCard icon={PackageMinus} label="Below minimum stock" value={data.low_stock.length} tone="blue" description="Based on eligible inventory" />
          <StatCard icon={CircleHelp} label="Expiry dates to review" value={data.expiry_review.length} tone="violet" description="Missing or unrecognized dates" />
          <StatCard icon={TriangleAlert} label="Expired lots" value={data.expired.length} tone="red" description="Excluded from usable stock" />
        </div>
        <div className="recommendation-stack">
          <section className="panel">
            <PanelHeading title="Inventory rules" subtitle={'Calculated from SQLite on ' + formatDate(data.as_of, true)} />
            <ul className="recommendation-body recommendation-rules">
              <li>Eligible stock has a positive lot balance and a recorded expiry date of today or later. Review storage and condition before use.</li>
              <li>First-expiring, first-out (FEFO) uses the earliest known expiry first, then the lower lot ID when dates match.</li>
              <li>Cost estimates use matching, positive unit costs on all eligible manual lots. Imported demo prices are excluded. Estimates use the recorded cost units and should be checked against supplier prices.</li>
              <li>Recommendations are advisory. Review purchases and update inventory through your usual workflow.</li>
            </ul>
          </section>
          <ExpiryPanel title="Use first" subtitle="Near-expiry lots in FEFO order." lots={data.near_expiry}
            empty="No eligible near-expiry lots" action="Prioritize this lot for use" />
          <Replenishment items={data.low_stock} />
          {canManage && <div className="recommendation-workspace">
            <PriorityRecipe lots={data.near_expiry} ids={data.recipe_ingredient_ids} />
            <Scenario ingredients={data.ingredients} />
          </div>}
          <ExpiryPanel title="Verify expiry dates" subtitle="Held out of usable stock, recipes, and scenarios until a date is recorded." lots={data.expiry_review}
            empty="No unknown expiry dates" action="Verify and record the expiry date" />
          <ExpiryPanel title="Review expired stock" subtitle="These lots are excluded from usable stock and recipes." lots={data.expired}
            empty="No expired lots with remaining stock" action="Separate from usable stock and review disposal" />
        </div>
      </>}
  </>
}
