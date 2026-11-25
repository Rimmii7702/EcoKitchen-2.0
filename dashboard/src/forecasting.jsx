import { AlertCircle, BarChart3, Beaker, CalendarDays, LineChart, Package } from 'lucide-react'
import { request, usePostAction, useResource } from './api'
import { formatDate } from './format'
import { Badge, DataTable, EmptyState, Loading, PageHeading, PanelHeading } from './ui'
import { RetailBenchmark } from './retail_benchmark'
import './forecasting.css'

const base = '/api/forecasting'
const loadForecasting = signal => request(base, { signal, service: 'forecasting' })
const loadBenchmark = signal => request(base + '/retail-benchmark', { signal, service: 'retail benchmark' })
const formatValue = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 })
const fields = {
  stock_quantity: 'Stock quantity',
  sales_volume: 'Assumed sales volume',
  days_to_expire: 'Days to expiry',
  unit_price: 'Unit price',
  inventory_turnover_rate: 'Inventory turnover rate',
}

function value(number) {
  return typeof number === 'number' && Number.isFinite(number) ? formatValue.format(number) : '—'
}

function ForecastError({ error, retry }) {
  return <div className="error-state" role="alert">
    <AlertCircle size={20} /><div><strong>Could not load a result</strong><p>{error.message ?? error}</p></div>
    {retry && <button className="button secondary" type="button" onClick={retry}>Try again</button>}
  </div>
}

function Unavailable({ title, description }) {
  return <div className="forecast-unavailable">
    <span><AlertCircle size={19} /></span><div><h3>{title}</h3><p>{description}</p></div>
  </div>
}

function SalesChart({ history, prediction, demo }) {
  const recent = history.slice(-26)
  const points = prediction ? [...recent, { ...prediction, units: prediction.predicted_units, forecast: true }] : recent
  const peak = Math.max(1, ...points.map(point => point.units))
  const tickStep = Math.max(1, Math.ceil(recent.length / 5))
  return (
    <>
      <figure className="forecast-chart">
        <div className="forecast-y-axis" aria-hidden="true"><span>{value(peak)}</span><span>{value(peak / 2)}</span><span>0</span></div>
        <div className="forecast-plot" role="img" aria-label={(demo ? 'Demo recorded sales' : 'Recorded weekly sales') +
          ' for ' + recent.length + ' periods' + (prediction ? ' and the next-week forecast' : '')}>
          {points.map((point, index) => (
            <div className="forecast-bar-slot" key={point.week_start}>
              <div title={formatDate(point.week_start) + ' · ' + value(point.units) + ' units'}
                className={'forecast-bar ' + (point.forecast ? 'forecast-bar-predicted' :
                  point.complete_period === false ? 'forecast-bar-partial' : '')}
                style={{ height: (point.units / peak * 100) + '%' }} />
              <span>{(index % tickStep === 0 || point.forecast) ? formatDate(point.week_start).replace(/,? \d{4}$/, '') : ''}</span>
            </div>
          ))}
        </div>
        <figcaption className="forecast-legend"><span><i /> Recorded units</span>
          {recent.some(point => point.complete_period === false) && <span><i className="partial" /> Partial period</span>}
          {prediction && <span><i className="predicted" /> Next-week forecast</span>}
        </figcaption>
      </figure>
      <details className="forecast-history">
        <summary>View recorded data</summary>
        <DataTable label="Weekly sales history" columns={demo ? ['Period start', 'Period end', 'Listed units', 'Dish rows'] :
          ['Week start', 'Week end', 'Units sold']}>
          {history.map(point => <tr key={point.week_start}>
            <td>{formatDate(point.week_start)}</td><td>{formatDate(point.week_end)}</td><td>{value(point.units)}</td>
            {demo && <td>{point.row_count}</td>}
          </tr>)}
        </DataTable>
      </details>
    </>
  )
}

function Metrics({ report, simulation = false }) {
  if (!report?.test) return null
  const test = report.split?.test
  const dateRange = test?.from && test.through
    ? ' · ' + formatDate(test.from) + ' to ' + formatDate(test.through) : ''
  return (
    <section className="panel forecast-metrics">
      <PanelHeading title={simulation ? 'Synthetic holdout results' : 'Demand holdout results'}
        subtitle={simulation ? report.split.test_samples + ' synthetic scenarios · errors in simulated units'
          : test?.weeks + ' weekly observations' + dateRange + ' · errors in units sold'} />
      <DataTable label={simulation ? 'Synthetic waste evaluation' : 'Demand forecasting evaluation'}
        columns={['Method', 'MAE', 'RMSE', simulation ? 'R²' : 'WAPE']}>
        {Object.entries(report.test).map(([key, metrics]) => <tr key={key}>
          <td><strong>{report.algorithms[key]}</strong>
            {!simulation && key === report.selected && <small className="cell-note">Selected on validation</small>}</td>
          <td>{value(metrics.mae)}</td><td>{value(metrics.rmse)}</td>
          <td>{simulation ? value(metrics.r2) : metrics.wape_pct == null ? '—' : value(metrics.wape_pct) + '%'}</td>
        </tr>)}
      </DataTable>
      {simulation && <p className="forecast-note">The target was generated from a formula using the input features. The midpoint rule is a fixed synthetic baseline.</p>}
      {!simulation && <p className="forecast-note">One-week rolling-origin test. Model selection used an earlier validation window.</p>}
    </section>
  )
}

function Demand({ data, canManage }) {
  const action = usePostAction({ service: 'forecasting' })
  const demo = data.source?.kind === 'self_made_demo'
  const history = data.history
  return (
    <>
      <div className="forecast-workspace">
        <section className="panel">
          <PanelHeading title={demo ? 'Recorded sales sample' : 'Weekly sales history'}
            subtitle={demo ? 'Totals cover only the dish rows listed for each period.' : 'Weekly totals from the configured sales export.'}>
            {data.source && <Badge tone={demo ? 'amber' : 'green'}>{demo ? 'Self-made demo data' : 'Operator-verified export'}</Badge>}
          </PanelHeading>
          {history.length > 0 ? <SalesChart history={history} prediction={action.data} demo={demo} /> :
            <EmptyState title="Sales history is unavailable" icon={BarChart3} />}
        </section>
        <section className="panel forecast-status">
          <PanelHeading title="Demand forecast" />
          {!data.available ? <Unavailable title="Forecast unavailable" description={data.reason} /> :
            <div className="forecast-action">
              <p>One week after the most recent observation in this export.</p>
              <button className="button primary" type="button" disabled={!canManage || action.busy}
                onClick={() => action.run(base + '/demand/predict', { horizon_weeks: 1 })}>
                <LineChart size={17} />Forecast next week
              </button>
              {action.busy && <Loading label="Running demand forecast" />}
              {action.error && <ForecastError error={action.error} />}
              {action.data && <div className="forecast-output" role="status">
                <strong>{value(action.data.predicted_units)} units</strong>
                <span>{formatDate(action.data.week_start)} to {formatDate(action.data.week_end)}</span>
                <span>{data.report.algorithms[action.data.model]}</span>
              </div>}
            </div>}
          {data.coverage && <dl className="forecast-facts">
            <div><dt>Listed rows</dt><dd>{data.coverage.rows}</dd></div>
            <div><dt>Recorded periods</dt><dd>{data.coverage.periods}</dd></div>
            <div><dt>Dish rows per period</dt><dd>{data.coverage.min_rows_per_period}–{data.coverage.max_rows_per_period}</dd></div>
            <div><dt>Most records for one dish</dt><dd>{data.coverage.max_rows_per_dish}</dd></div>
          </dl>}
          {data.source && <p className="forecast-note">Source: {data.source.name}</p>}
        </section>
      </div>
      <Metrics report={data.report} />
    </>
  )
}

function Waste({ data, canManage }) {
  const action = usePostAction({ service: 'forecasting' })
  function submit(event) {
    event.preventDefault()
    if (!canManage) return
    const form = new FormData(event.currentTarget)
    const payload = Object.fromEntries(Object.keys(fields).map(key => [key, Number(form.get(key))]))
    payload.category = form.get('category')
    action.run(base + '/waste-simulation/predict', payload)
  }

  return (
    <>
      <section className="panel forecast-measured">
        <PanelHeading title="Measured food waste" />
        <Unavailable title="Measured waste prediction unavailable" description={data.reason} />
      </section>
      <div className="forecast-workspace">
        <section className="panel">
          <PanelHeading title="Synthetic scenario" subtitle="Enter assumptions to run the experimental model.">
            <Badge tone="violet">Simulation only</Badge>
          </PanelHeading>
          {data.simulation_available ? <form className="forecast-form" onSubmit={submit}>
            <fieldset disabled={!canManage || action.busy}>
              <legend className="sr-only">Synthetic waste scenario inputs</legend>
              {Object.entries(fields).map(([key, label]) =>
                <label key={key}>
                  <span>{label}</span>
                  <input name={key} type="number" required min={data.fields[key].min} max={data.fields[key].max}
                    step={data.fields[key].step} />
                </label>)}
              <label><span>Category</span><select name="category" required defaultValue="">
                <option value="">Select a category</option>
                {data.fields.category.options.map(category => <option key={category}>{category}</option>)}
              </select></label>
            </fieldset>
            <button className="button primary" type="submit" disabled={!canManage || action.busy}>
              <Beaker size={16} />Run simulation
            </button>
          </form> : <Unavailable title="Simulation model unavailable" description={data.simulation_reason} />}
        </section>
        <section className="panel">
          <PanelHeading title="Scenario result" />
          {action.busy ? <Loading label="Running synthetic scenario" /> : action.error ?
            <ForecastError error={action.error} /> : action.data ?
              <div className="forecast-output scenario-output" role="status">
                <Badge tone="violet">Synthetic result</Badge><strong>{value(action.data.simulated_waste_units)}</strong>
                <span>Simulated waste units</span>
              </div> :
              <EmptyState title="No scenario result yet" description="The result appears after you run the simulation." icon={Package} />}
        </section>
      </div>
      <Metrics report={data.report} simulation />
    </>
  )
}

export function ForecastingPage({ revision, params, canManage }) {
  const requested = params?.get('view')
  const tab = requested === 'waste' || requested === 'retail' ? requested : 'demand'
  const resource = useResource(tab === 'retail' ? loadBenchmark : loadForecasting, revision)
  const select = view => { window.location.hash = view === 'demand' ? 'forecasting' : 'forecasting?view=' + view }
  return (
    <>
      <PageHeading title="Forecasting" description="Review sales history, model evaluations, and synthetic waste scenarios." />
      <div className="vision-tabs" role="group" aria-label="Forecasting views">
        <button type="button" className={'vision-tab ' + (tab === 'demand' ? 'selected' : '')}
          aria-pressed={tab === 'demand'} onClick={() => select('demand')}><CalendarDays size={18} /> Demand</button>
        <button type="button" className={'vision-tab ' + (tab === 'waste' ? 'selected' : '')}
          aria-pressed={tab === 'waste'} onClick={() => select('waste')}><Beaker size={18} /> Waste prediction</button>
        <button type="button" className={'vision-tab ' + (tab === 'retail' ? 'selected' : '')}
          aria-pressed={tab === 'retail'} onClick={() => select('retail')}><BarChart3 size={18} /> Retail Demand Benchmark</button>
      </div>
      {resource.loading ? <Loading label={tab === 'retail' ? 'Loading retail benchmark' : 'Reading forecasting models and data'} /> : resource.error ?
        <ForecastError error={resource.error} retry={resource.retry} /> : tab === 'retail' ?
          <RetailBenchmark data={resource.data} /> : tab === 'demand' ?
          <Demand data={resource.data.demand} canManage={canManage} /> : <Waste data={resource.data.waste} canManage={canManage} />}
    </>
  )
}
