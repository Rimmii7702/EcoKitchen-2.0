import { Activity, ArrowDownRight, BarChart3, CalendarDays, Compass, Database, Gauge, Target } from 'lucide-react'
import { formatDate } from './format'
import { Badge, DataTable, EmptyState, PanelHeading, StatCard } from './ui'
import './retail_benchmark.css'

const decimal = new Intl.NumberFormat('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })
const totals = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })
const compactDate = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
const percent = number => decimal.format(number) + '%'
const signedPercent = number => (number > 0 ? '+' : '') + percent(number)

const series = [
  { key: 'actual', label: 'Observed sales', tone: 'actual' },
  { key: 'seasonal_naive', label: 'Seasonal naive', tone: 'naive' },
  { key: 'lightgbm', label: 'LightGBM', tone: 'lightgbm' },
]
const models = series.slice(1)

function Chart({ label, data, lines, step, format, tick, unit }) {
  const values = data.flatMap(row => lines.map(line => row[line.key]))
  const low = Math.max(0, Math.floor(Math.min(...values) / step) * step)
  const high = Math.max(low + step, Math.ceil(Math.max(...values) / step) * step)
  const intervals = Math.min(5, Math.max(1, Math.round((high - low) / step)))
  const levels = Array.from({ length: intervals + 1 }, (_, index) => low + (high - low) * (index / intervals))
  const x = index => 64 + index * 494 / Math.max(1, data.length - 1)
  const y = amount => 192 - (amount - low) * 170 / (high - low)
  return <figure className="retail-chart">
    <p className="retail-chart-unit">{unit}</p>
    <svg role="img" aria-label={label} viewBox="0 0 588 226">
      {levels.map(level => <g key={level}>
        <line className="retail-gridline" x1="63" x2="558" y1={y(level)} y2={y(level)} />
        <text x="54" y={y(level) + 4} textAnchor="end">{tick(level)}</text>
      </g>)}
      {lines.map(line => <g key={line.key} className={'retail-series ' + line.tone}>
        <polyline points={data.map((row, index) => x(index) + ',' + y(row[line.key])).join(' ')} />
        {data.map((row, index) => <circle key={row.date} cx={x(index)} cy={y(row[line.key])} r="4">
          <title>{line.label} · {row.label} · {format(row[line.key])}</title>
        </circle>)}
      </g>)}
      {data.map((row, index) => <text key={row.date} x={x(index)} y="215" textAnchor="middle">{row.label}</text>)}
    </svg>
    <figcaption className="retail-legend">{lines.map(line => <span key={line.key}>
      <i className={'retail-series ' + line.tone} aria-hidden="true" />{line.label}
    </span>)}</figcaption>
  </figure>
}

function ChartPanel({ title, subtitle, disclosure, chart, columns }) {
  return <section className="panel retail-chart-panel">
    <PanelHeading title={title} subtitle={subtitle} />
    <Chart {...chart} />
    <details className="forecast-history">
      <summary>{disclosure}</summary>
      <DataTable label={chart.label + ' data'} columns={columns}>
        {chart.data.map(row => <tr key={row.date}>
          <th scope="row">{formatDate(row.date)}</th>
          {chart.lines.map(line => <td key={line.key}>{chart.format(row[line.key])}</td>)}
        </tr>)}
      </DataTable>
    </details>
  </section>
}

function Comparison({ data }) {
  return <section className="panel">
    <PanelHeading title="Model comparison"
      subtitle="Both models scored the same holdout rows. Lower MAE, RMSE, and WAPE indicate less error." />
    <DataTable label="Holdout model comparison" columns={['Model', 'MAE', 'RMSE', 'WAPE', 'Signed bias']}>
      {models.map(({ key, label, tone }) => {
        const metric = data.models[key].metrics
        return <tr key={key}>
          <th scope="row"><span className={'retail-model retail-series ' + tone}><i />{label}</span></th>
          <td>{decimal.format(metric.mae)}</td><td>{decimal.format(metric.rmse)}</td>
          <td><strong>{percent(metric.wape_pct)}</strong></td><td>{signedPercent(metric.signed_bias_pct)}</td>
        </tr>
      })}
    </DataTable>
    <p className="forecast-note">Negative signed bias means the forecast total was below observed sales.</p>
  </section>
}

function StockoutComparison({ groups }) {
  const scale = Math.max(10, Math.ceil(Math.max(...groups.flatMap(group => models.map(({ key }) => group[key].wape_pct))) / 10) * 10)
  return <section className="panel">
    <PanelHeading title="Errors with and without stockouts"
      subtitle={'WAPE by 24-hour stockout status · bars share a 0–' + scale + '% scale'} />
    <div className="retail-cohorts">
      {groups.map(group => <article key={group.id}>
        <h3>{group.label}</h3>
        <p>{totals.format(group.rows)} evaluation records</p>
        {models.map(({ key, label, tone }) => <div key={key} className="retail-cohort-row">
          <span>{label}</span><strong>{percent(group[key].wape_pct)}</strong>
          <span className="retail-meter" aria-hidden="true">
            <span className={'retail-series ' + tone} style={{ width: (group[key].wape_pct / scale * 100) + '%' }} />
          </span>
        </div>)}
      </article>)}
    </div>
    <p className="forecast-note">Observed sales may underestimate true demand. Demand during stockouts remains unobserved.</p>
  </section>
}

export function RetailBenchmark({ data }) {
  if (!data?.daily_sales?.length || !data?.horizons?.length) {
    return <section className="panel"><EmptyState title="No retail benchmark results" icon={BarChart3}
      description="The dashboard needs the completed FreshRetailNet-50K holdout scorecard to show this benchmark." /></section>
  }
  const { dataset, evaluation, models: results } = data
  const lightgbm = results.lightgbm.metrics
  const difference = data.wape_improvement_pp
  const dates = evaluation.start === evaluation.end ? formatDate(evaluation.start) :
    compactDate.format(new Date(evaluation.start)) + ' – ' + formatDate(evaluation.end)
  const daily = data.daily_sales.map(row => ({ ...row, label: compactDate.format(new Date(row.date)) }))
  const horizons = data.horizons.map(row => ({
    date: row.date, label: 'Day ' + row.day,
    seasonal_naive: row.seasonal_naive.wape_pct, lightgbm: row.lightgbm.wape_pct,
  }))
  return <div className="retail-benchmark">
    <section className="retail-hero" aria-labelledby="retail-title">
      <div className="retail-intro">
        <Badge tone="green">Completed official holdout</Badge>
        <h2 id="retail-title">Retail Demand Benchmark</h2>
        <p>{dataset.name} from {dataset.publisher} · {dates}</p>
        <p>Historical fresh grocery retail study of normalized observed retail sales, separate from restaurant forecasting.</p>
      </div>
      <div className="retail-improvement">
        {difference >= 0 && <ArrowDownRight size={23} aria-hidden="true" />}
        <strong>{decimal.format(Math.abs(difference))} pp</strong>
        <span>{difference === 0 ? 'WAPE difference' : difference > 0 ? 'Lower WAPE' : 'Higher WAPE'} vs. seasonal naive</span>
        <small>Frozen {results.lightgbm.trees}-tree CPU LightGBM</small>
      </div>
    </section>
    <div className="stat-grid retail-stats" aria-label="LightGBM holdout summary">
      <StatCard icon={Database} tone="blue" label="Evaluation records" value={totals.format(evaluation.rows)}
        description={totals.format(evaluation.pairs) + ' store–product pairs'} />
      <StatCard icon={CalendarDays} tone="blue" label="Forecast horizon" value={evaluation.horizon_days + ' days'}
        description={'Issued ' + formatDate(evaluation.forecast_origin)} />
      <StatCard icon={Target} tone="green" label="MAE" value={decimal.format(lightgbm.mae)} description="Normalized sales units" />
      <StatCard icon={Activity} tone="green" label="RMSE" value={decimal.format(lightgbm.rmse)} description="Normalized sales units" />
      <StatCard icon={Gauge} tone="green" label="WAPE" value={percent(lightgbm.wape_pct)} description="Error relative to observed sales" />
      <StatCard icon={Compass} tone="amber" label="Signed bias" value={signedPercent(lightgbm.signed_bias_pct)}
        description={lightgbm.signed_bias_pct < 0 ? 'Overall underforecast' : lightgbm.signed_bias_pct > 0 ? 'Overall overforecast' : 'No aggregate bias'} />
    </div>
    <div className="retail-charts">
      <ChartPanel title="Observed vs. predicted sales" disclosure="View daily sales data"
        subtitle={'Daily totals across all ' + totals.format(evaluation.pairs) + ' store–product pairs.'}
        columns={['Date', 'Observed sales', 'Seasonal naive', 'LightGBM']}
        chart={{ label: 'Daily observed and predicted sales', data: daily, lines: series, step: 10000,
          format: totals.format, tick: number => number / 1000 + 'k', unit: evaluation.units }} />
      <ChartPanel title="WAPE by forecast horizon" disclosure="View horizon WAPE data"
        subtitle="One fixed forecast origin for all seven days."
        columns={['Date', 'Seasonal naive WAPE', 'LightGBM WAPE']}
        chart={{ label: 'Seven-day WAPE comparison', data: horizons, lines: models, step: 10,
          format: percent, tick: number => number + '%', unit: 'Weighted absolute percentage error' }} />
    </div>
    <div className="retail-bottom"><Comparison data={data} /><StockoutComparison groups={data.stockout_groups} /></div>
    <section className="panel retail-source">
      <PanelHeading title="Dataset and evaluation scope" />
      <p>This is a completed historical evaluation of {dataset.domain.toLowerCase()}. Sales and forecast amounts use {evaluation.units}.
        The benchmark runs independently of live forecasts, inventory quantities, and replenishment recommendations.</p>
      <p>Published by {dataset.publisher}. <a href={dataset.url} target="_blank" rel="noreferrer">Dataset and methodology</a>
        <span aria-hidden="true"> · </span><a href={dataset.license_url} target="_blank" rel="noreferrer">{dataset.license} license</a></p>
    </section>
  </div>
}
