import { useId, useState } from "react";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  DailyStatisticRow,
  DailyStatisticsOut,
  DailyStatisticVariable,
  StatisticSummary,
  formatDay,
} from "../api";

const SOURCE_LABELS: Record<string, string> = { checkin: "Check-in", linguistic: "Análisis textual", psychosocial: "Análisis psicosocial" };
const AGGREGATION_LABELS: Record<string, string> = { mean: "Media diaria", any: "Presencia en el día", counts: "Frecuencia de categorías" };
const formatNumber = (value: unknown, digits = 2) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("es-ES", { maximumFractionDigits: digits })
    : "—";
const formatRate = (value?: number | null) =>
  typeof value === "number" && Number.isFinite(value) ? `${formatNumber(value * 100, 1)} %` : "—";

function numericValue(row: DailyStatisticRow, key: string): number | null {
  const value = row[key];
  if (typeof value === "boolean") return Number(value);
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function CategoryCounts({ stats }: { stats?: StatisticSummary }) {
  const entries = Object.entries(stats?.counts ?? {});
  if (!entries.length) return <>—</>;
  return (
    <span className="statistics-category-counts">
      {entries.map(([label, count]) => <span key={label}>{label}: {count}</span>)}
    </span>
  );
}

function SummaryReading({ variable, stats }: { variable: DailyStatisticVariable; stats?: StatisticSummary }) {
  if (!stats?.n) return <>Sin datos</>;
  if (variable.kind === "categorical") return <CategoryCounts stats={stats} />;
  if (variable.kind === "boolean") {
    return <>{stats.true_count ?? 0} / {stats.n} días con indicio ({formatRate(stats.rate)})</>;
  }
  return <>{formatNumber(stats.mean)}{variable.unit === "h" ? " h" : ""}</>;
}

/** Values and statistics come from the server, using its daily buckets and
 * missing-value rules. The chart only plots available same-date pairs. */
export default function DailyStatisticsPanel({
  data,
}: {
  data?: DailyStatisticsOut | null;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [date, setDate] = useState("");
  const [xKey, setXKey] = useState("mood");
  const [yKey, setYKey] = useState("sleep_hours");
  if (!data) {
    return <p className="meta">Las estadísticas diarias todavía no están disponibles.</p>;
  }

  const variables = data.variables;
  const filtered = variables.filter((variable) => `${variable.label} ${variable.key}`.toLocaleLowerCase("es").includes(query.toLocaleLowerCase("es")));
  const correlationKeys = new Set(data.correlations.flatMap((pair) => [pair.x, pair.y]));
  const numeric = variables.filter((variable) => variable.kind !== "categorical" && correlationKeys.has(variable.key));
  const rows = data.daily;
  const selectedDay = rows.find((row) => row.date === date) ?? rows[rows.length - 1];
  const selectedIndex = selectedDay ? rows.indexOf(selectedDay) : -1;
  const xVariable = numeric.find((variable) => variable.key === xKey) ?? numeric[0];
  const yVariable = numeric.find((variable) => variable.key === yKey) ?? numeric[1] ?? numeric[0];
  const correlation = xVariable && yVariable ? data.correlations.find((pair) =>
    (pair.x === xVariable.key && pair.y === yVariable.key) ||
    (pair.y === xVariable.key && pair.x === yVariable.key)
  ) : undefined;
  const pairs = xVariable && yVariable ? rows.flatMap((row) => {
    const x = numericValue(row, xVariable.key);
    const y = numericValue(row, yVariable.key);
    return x === null || y === null ? [] : [{ date: row.date, x, y }];
  }) : [];

  return (
    <section className="statistics-panel" aria-labelledby={`${id}-title`}>
      <div className="statistics-heading">
        <h3 id={`${id}-title`}>Estadística diaria y relaciones entre variables</h3>
        <span className="statistics-badge">Análisis exploratorio</span>
      </div>
      <p className="meta">
        Ventana de {data.window_days} días · fechas en {data.timezone}. Las medias del período dan el mismo
        peso a cada día con datos. «—» significa sin dato, nunca cero.
      </p>
      <p className="statistics-caution">
        Las señales textuales son inferencias pendientes de valoración clínica. Estas estadísticas y correlaciones no son escalas validadas, no predicen por sí solas suicidio o recaída y no modifican las reglas de seguridad.
      </p>
      {!rows.length ? <p>Sin observaciones en esta ventana.</p> : (
        <>
          <label className="statistics-filter">
            Filtrar variables
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ánimo, valencia, ideación, rumiación…" />
          </label>
          <h4>Resumen del período</h4>
          <div className="table-wrap">
            <table className="table statistics-table">
              <caption>Medias y dispersión entre días; frecuencias para indicios y categorías.</caption>
              <thead>
                <tr><th scope="col">Variable</th><th scope="col">Días con datos</th><th scope="col">Media / frecuencia</th><th scope="col">DE</th><th scope="col">Mín. – máx.</th><th scope="col">Días sin datos</th></tr>
              </thead>
              <tbody>
                {filtered.map((variable) => {
                  const stats = data.summary[variable.key];
                  return (
                    <tr key={variable.key}>
                      <th scope="row"><span>{variable.label}{variable.unit ? ` (${variable.unit})` : ""}</span><small>{SOURCE_LABELS[variable.source] ?? variable.source} · {AGGREGATION_LABELS[variable.aggregation] ?? variable.aggregation}</small></th>
                      <td>{variable.kind === "categorical" ? (stats?.observed_days ?? "—") : (stats?.n ?? 0)}</td>
                      <td><SummaryReading variable={variable} stats={stats} /></td>
                      <td>{variable.kind === "numeric" ? formatNumber(stats?.sd) : "—"}</td>
                      <td>{variable.kind === "numeric" && stats?.n ? `${formatNumber(stats.min)} – ${formatNumber(stats.max)}` : "—"}</td>
                      <td>{stats?.missing_days ?? "—"}</td>
                    </tr>
                  );
                })}
                {!filtered.length && <tr><td colSpan={6}>Ninguna variable coincide con el filtro.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="meta">DE: desviación estándar muestral; requiere al menos dos valores. En categorías se cuentan observaciones, por lo que un mismo día puede aportar varias.</p>

          <details className="statistics-details" open>
            <summary>Desglose por fecha</summary>
            {selectedDay && (
              <>
                <div className="statistics-date-controls">
                  <button type="button" className="secondary" disabled={selectedIndex <= 0} onClick={() => setDate(rows[selectedIndex - 1].date)} aria-label="Fecha anterior">←</button>
                  <label>
                    Fecha de observación
                    <select value={selectedDay.date} onChange={(event) => setDate(event.target.value)}>
                      {rows.map((row) => <option key={row.date} value={row.date}>{formatDay(row.date)}</option>)}
                    </select>
                  </label>
                  <button type="button" className="secondary" disabled={selectedIndex >= rows.length - 1} onClick={() => setDate(rows[selectedIndex + 1].date)} aria-label="Fecha siguiente">→</button>
                </div>
                <p className="meta">
                  {selectedDay.counts.checkins ?? 0} check-ins
                  {" "}· {selectedDay.counts.interactions ?? 0} interacciones analizadas · {selectedDay.counts.psychosocial_observations ?? 0} observaciones psicosociales.
                </p>
                <div className="table-wrap">
                  <table className="table statistics-table">
                    <caption>Datos de {formatDay(selectedDay.date)}. Las variables numéricas son medias del día; un indicio se marca si aparece al menos una vez.</caption>
                    <thead><tr><th scope="col">Variable</th><th scope="col">Valor diario</th><th scope="col">Observaciones</th><th scope="col">DE del día</th></tr></thead>
                    <tbody>
                      {filtered.map((variable) => {
                        const stats = selectedDay.statistics[variable.key] ?? selectedDay.categories[variable.key];
                        const value = selectedDay[variable.key];
                        return (
                          <tr key={variable.key}>
                            <th scope="row">{variable.label}{variable.unit ? ` (${variable.unit})` : ""}</th>
                            <td>{variable.kind === "categorical" ? <CategoryCounts stats={stats} /> : variable.kind === "boolean" ? (
                              value === null || value === undefined ? "Sin datos" : value ? `Indicio presente (${stats?.true_count ?? "—"}/${stats?.n ?? "—"})` : "No marcado en los análisis disponibles"
                            ) : `${formatNumber(value)}${typeof value === "number" && variable.unit === "h" ? " h" : ""}`}</td>
                            <td>{stats?.n ?? 0}</td>
                            <td>{variable.kind === "numeric" ? formatNumber(stats?.sd) : "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </details>

          {numeric.length > 1 && xVariable && yVariable && (
            <details className="statistics-details">
              <summary>Correlaciones entre variables</summary>
              <p className="meta">Pearson sobre fechas con ambos datos, sin rellenar ausencias. Los indicios se codifican como 1 (presente) o 0 (no marcado). Se necesitan al menos tres pares y variación en ambas series. No se calculan probabilidades clínicas ni significación estadística.</p>
              <div className="statistics-pair-controls">
                <label>Variable X<select value={xVariable.key} onChange={(event) => setXKey(event.target.value)}>{numeric.map((variable) => <option key={variable.key} value={variable.key}>{variable.label}</option>)}</select></label>
                <label>Variable Y<select value={yVariable.key} onChange={(event) => setYKey(event.target.value)}>{numeric.map((variable) => <option key={variable.key} value={variable.key}>{variable.label}</option>)}</select></label>
              </div>
              <p className="statistics-pair-result" role="status">
                {xVariable.key === yVariable.key ? "Selecciona dos variables diferentes." : !correlation || correlation.status === "insufficient_pairs" ? `No calculable: se necesitan al menos 3 fechas con ambos datos (${correlation?.n ?? pairs.length} disponibles).` : correlation.status === "constant_series" ? `No calculable: al menos una serie es constante (${correlation.n} pares).` : `r = ${formatNumber(correlation.r, 3)} · ${correlation.n} fechas con ambos datos.`}
              </p>
              {pairs.length > 0 && xVariable.key !== yVariable.key && (
                <div className="statistics-scatter">
                  <p className="meta">Eje X: {xVariable.label}{xVariable.unit ? ` (${xVariable.unit})` : ""} · Eje Y: {yVariable.label}{yVariable.unit ? ` (${yVariable.unit})` : ""}. Cada punto es una fecha.</p>
                  <ResponsiveContainer width="100%" height={260}>
                    <ScatterChart margin={{ top: 8, right: 24, bottom: 8, left: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis type="number" dataKey="x" name={xVariable.label} unit={xVariable.unit === "h" ? " h" : ""} tick={{ fontSize: 11 }} domain={xVariable.kind === "boolean" ? [0, 1] : ["auto", "auto"]} />
                      <YAxis type="number" dataKey="y" name={yVariable.label} unit={yVariable.unit === "h" ? " h" : ""} tick={{ fontSize: 11 }} domain={yVariable.kind === "boolean" ? [0, 1] : ["auto", "auto"]} />
                      <Tooltip content={({ active, payload }) => {
                        const point = payload?.[0]?.payload as { date: string; x: number; y: number } | undefined;
                        return active && point ? <div className="statistics-tooltip"><strong>{formatDay(point.date)}</strong><p>{xVariable.label}: {formatNumber(point.x)}</p><p>{yVariable.label}: {formatNumber(point.y)}</p></div> : null;
                      }} />
                      <Scatter data={pairs} fill="#3987e5" />
                    </ScatterChart>
                  </ResponsiveContainer>
                </div>
              )}
            </details>
          )}
          {data.notes.length > 0 && (
            <details className="statistics-details">
              <summary>Método, fuentes y límites</summary>
              <ul>{data.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>
              <p>Las explicaciones libres, las citas y la justificación del análisis se consultan en Evidencia; no se convierten en puntuaciones.</p>
            </details>
          )}
        </>
      )}
    </section>
  );
}
