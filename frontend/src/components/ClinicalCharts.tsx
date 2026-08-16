/**
 * Charts for the professional panel.
 *
 * Every chart here answers one clinical question and carries its own
 * caption saying how to read it. That caption is not decoration: the
 * previous panel showed a bare `structural_score` number that readers
 * consistently interpreted as "risk", when it actually measures similarity
 * to the patient's own baseline.
 */
import { ReactNode } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  CheckInPoint,
  LevelPoint,
  LinguisticPoint,
  MetricEvent,
  PsychosocialPoint,
  StructuralPoint,
  formatDay,
} from "../api";

const COLORS = {
  level: "#c1121f",
  score: "#2e7d32",
  mood: "#4f8ef7",
  craving: "#f76c4f",
  sleep: "#9b59b6",
  efficacy: "#0f9b8e",
  rumination: "#d1495b",
  valence: "#8d6a9f",
  urgency: "#e07a1f",
  ambivalence: "#5c6f7d",
  neutral: "#9aa5b1",
};

export function ChartCard({
  title,
  question,
  howToRead,
  empty,
  children,
  footer,
}: {
  title: string;
  question: string;
  howToRead: string;
  empty?: boolean;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <section className="chart-card">
      <header className="chart-card-head">
        <h3>{title}</h3>
        <p className="chart-question">{question}</p>
      </header>
      {empty ? (
        <p className="chart-empty">Todavía no hay datos suficientes para dibujar esta gráfica.</p>
      ) : (
        <div className="chart-body">{children}</div>
      )}
      <p className="chart-legend-note">
        <strong>Cómo se lee:</strong> {howToRead}
      </p>
      {footer}
    </section>
  );
}

/** Alert level over time. The single most important series for triage. */
export function LevelHistoryChart({
  daily,
  levels,
}: {
  daily: { date: string; max_level: number }[];
  levels: LevelPoint[];
}) {
  const byDate = new Map(levels.map((row) => [row.date, row]));
  const data = daily.map((row) => ({ ...row, detail: byDate.get(row.date) }));
  return (
    <ChartCard
      title="Nivel de alarma en el tiempo"
      question="¿Cuándo y con qué frecuencia ha escalado este paciente?"
      howToRead="Un punto por día con actividad, con el nivel MÁS ALTO alcanzado ese día (0–4). Las mesetas en 3–4 son lo que hay que mirar; los picos aislados suelen ser un único texto. El nivel lo decide siempre el motor determinista, nunca el modelo de lenguaje."
      empty={data.length === 0}
    >
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -18 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <ReferenceArea y1={3} y2={4} fill={COLORS.level} fillOpacity={0.07} />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatDay} minTickGap={24} />
          <YAxis domain={[0, 4]} ticks={[0, 1, 2, 3, 4]} tick={{ fontSize: 11 }} />
          <Tooltip
            labelFormatter={formatDay}
            formatter={(value: unknown, _name, item) => {
              const detail = (item?.payload as { detail?: LevelPoint } | undefined)?.detail;
              return [`Nivel ${value}${detail?.reason ? ` — ${detail.reason}` : ""}`, "Máximo del día"];
            }}
          />
          <Area
            type="stepAfter"
            dataKey="max_level"
            name="Nivel máximo del día"
            stroke={COLORS.level}
            fill={COLORS.level}
            fillOpacity={0.18}
            strokeWidth={2}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** Structural score with its band thresholds drawn in. */
export function StructuralScoreChart({ points }: { points: StructuralPoint[] }) {
  return (
    <ChartCard
      title="Score estructural (similitud con su línea base)"
      question="¿Se están alejando sus check-ins de lo que es habitual en él o ella?"
      howToRead="1.00 = sus últimos 7 días son indistinguibles de sus 21 días previos. 0.00 = se han alejado mucho. NO es una escala de riesgo: un score alto significa «sin cambios», no «sin peligro». Verde ≥ 0.60 estable · amarillo 0.35–0.60 transición · rojo < 0.35 inestable."
      empty={points.length === 0}
    >
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4, left: -18 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <ReferenceArea y1={0.6} y2={1} fill="#2e7d32" fillOpacity={0.06} />
          <ReferenceArea y1={0.35} y2={0.6} fill="#e0a800" fillOpacity={0.08} />
          <ReferenceArea y1={0} y2={0.35} fill="#c1121f" fillOpacity={0.07} />
          <ReferenceLine y={0.6} stroke="#2e7d32" strokeDasharray="4 4" />
          <ReferenceLine y={0.35} stroke="#c1121f" strokeDasharray="4 4" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatDay} minTickGap={24} />
          <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
          <Tooltip
            labelFormatter={formatDay}
            formatter={(value: unknown, _n, item) => {
              const band = (item?.payload as StructuralPoint | undefined)?.band;
              return [`${value}${band ? ` (${band})` : ""}`, "Score estructural"];
            }}
          />
          <Line
            type="monotone"
            dataKey="score"
            name="Score estructural"
            stroke={COLORS.score}
            strokeWidth={2}
            connectNulls
            dot={{ r: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** Per-variable z-scores — this is what makes a low score actionable. */
export function ZScoreChart({ points }: { points: StructuralPoint[] }) {
  return (
    <ChartCard
      title="Desviación por variable (z-scores)"
      question="Si se ha desviado, ¿QUÉ se ha desviado y en qué dirección?"
      howToRead="0 = igual que su línea base. Por DEBAJO de 0 = peor que su normalidad (menos ánimo, más craving, menos sueño, menos autoeficacia). Por ENCIMA de 0 = mejor. El score estructural usa el valor absoluto, así que una mejora grande también baja el score: esta gráfica es la que dice si el cambio es a mejor o a peor."
      empty={points.length === 0}
    >
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4, left: -18 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <ReferenceLine y={0} stroke="#333" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatDay} minTickGap={24} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip labelFormatter={formatDay} />
          <Legend />
          <Line type="monotone" dataKey="z_mood" name="Ánimo" stroke={COLORS.mood} connectNulls dot={false} />
          <Line
            type="monotone"
            dataKey="z_craving_inv"
            name="Craving (invertido)"
            stroke={COLORS.craving}
            connectNulls
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="z_sleep_hours"
            name="Sueño"
            stroke={COLORS.sleep}
            connectNulls
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="z_self_efficacy"
            name="Autoeficacia"
            stroke={COLORS.efficacy}
            connectNulls
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function CheckInChart({ points }: { points: CheckInPoint[] }) {
  return (
    <ChartCard
      title="Check-ins declarados"
      question="¿Qué está reportando el paciente día a día?"
      howToRead="Valores crudos tal y como los introduce el paciente, sin transformar. Ánimo, craving y autoeficacia en 0–10 (eje izquierdo); horas de sueño en el eje derecho. Craving alto es malo; ánimo y autoeficacia altos son buenos."
      empty={points.length === 0}
    >
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 4, left: -18 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatDay} minTickGap={24} />
          <YAxis yAxisId="left" domain={[0, 10]} tick={{ fontSize: 11 }} />
          <YAxis yAxisId="right" orientation="right" domain={[0, 12]} tick={{ fontSize: 11 }} />
          <Tooltip labelFormatter={formatDay} />
          <Legend />
          <Line yAxisId="left" type="monotone" dataKey="mood" name="Ánimo" stroke={COLORS.mood} connectNulls />
          <Line
            yAxisId="left"
            type="monotone"
            dataKey="craving"
            name="Craving"
            stroke={COLORS.craving}
            connectNulls
          />
          <Line
            yAxisId="left"
            type="monotone"
            dataKey="self_efficacy"
            name="Autoeficacia"
            stroke={COLORS.efficacy}
            connectNulls
          />
          <Line
            yAxisId="right"
            type="monotone"
            dataKey="sleep_hours"
            name="Sueño (h)"
            stroke={COLORS.sleep}
            strokeDasharray="5 3"
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/**
 * Agent 2 output over time. Points where the model raised a flag are drawn
 * as an extra scatter series so they are visible at a glance and can be
 * traced back to the sentence that produced them.
 */
export function LinguisticSignalChart({
  points,
  onSelect,
}: {
  points: LinguisticPoint[];
  onSelect?: (point: LinguisticPoint) => void;
}) {
  const data = points.map((point) => ({
    ...point,
    flagged:
      point.ideation_direct || point.ideation_indirect || point.consumption_crisis ? 1 : null,
  }));
  return (
    <ChartCard
      title="Señales lingüísticas del Agente 2"
      question="¿Cómo está escribiendo el paciente en chat y diario?"
      howToRead="Un punto por cada texto analizado (chat o diario), en escala 0–1. Son INFERENCIAS de un modelo de lenguaje, no medidas. Los rombos en la línea superior marcan textos donde el modelo levantó una bandera (ideación o crisis de consumo): pincha en un punto para abrir el texto original en la pestaña Evidencia."
      empty={data.length === 0}
      footer={
        <p className="chart-footnote">
          Ningún valor de esta gráfica decide por sí solo el nivel de alarma; solo{" "}
          <code>ideation_direct</code> y <code>consumption_crisis</code> entran en una regla, y solo si el
          texto tiene menos de 12 h.
        </p>
      }
    >
      <ResponsiveContainer width="100%" height={250}>
        <ComposedChart
          data={data}
          margin={{ top: 8, right: 16, bottom: 4, left: -18 }}
          onClick={(state) => {
            const index = state?.activeTooltipIndex;
            if (onSelect && typeof index === "number" && data[index]) onSelect(data[index]);
          }}
        >
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatDay} minTickGap={24} />
          <YAxis domain={[0, 1.12]} ticks={[0, 0.25, 0.5, 0.75, 1]} tick={{ fontSize: 11 }} />
          <Tooltip
            labelFormatter={formatDay}
            formatter={(value: unknown, name) =>
              name === "Bandera crítica" ? ["sí", name] : [value as number, name]
            }
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="rumination_score"
            name="Rumiación"
            stroke={COLORS.rumination}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="negative_valence"
            name="Valencia negativa"
            stroke={COLORS.valence}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="urgency_level"
            name="Urgencia"
            stroke={COLORS.urgency}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="ambivalence"
            name="Ambivalencia"
            stroke={COLORS.ambivalence}
            strokeDasharray="4 3"
            connectNulls
          />
          <Scatter dataKey="flagged" name="Bandera crítica" fill={COLORS.level} shape="diamond" />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** The social ground the patient is standing on, over time.
 *
 *  Plotted from the snapshot each risk decision stored, so the line is what
 *  the engine actually saw on each day rather than a recomputation from
 *  today's observations.
 */
export function PsychosocialIndexChart({ points }: { points: PsychosocialPoint[] }) {
  const flagged = points.map((point) => ({
    ...point,
    rupture: point.acute_deterioration.length > 0 || point.leave_taking_signal ? 1 : null,
  }));
  return (
    <ChartCard
      title="Contexto psicosocial en el tiempo"
      question="¿Se le está estrechando el suelo — apoyos, dinero, vivienda — mientras hablamos de síntomas?"
      howToRead="Cuatro índices de 0 a 1 construidos con umbrales fijos a partir de lo que el paciente ha contado. El apoyo es la única línea donde MÁS ALTO ES MEJOR; en las otras tres, más alto es peor. Los rombos marcan días con un deterioro reciente o una señal de despedida. Estas curvas no dependen de los check-ins: por eso pueden moverse con el score estructural completamente plano."
      empty={points.length === 0}
    >
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={flagged} margin={{ top: 8, right: 16, bottom: 4, left: -18 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <ReferenceLine y={0.66} stroke={COLORS.level} strokeDasharray="4 4" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={formatDay} minTickGap={24} />
          <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
          <Tooltip labelFormatter={formatDay} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Line
            type="monotone"
            dataKey="support_index"
            name="Apoyo (más alto, mejor)"
            stroke={COLORS.score}
            strokeWidth={2}
            connectNulls
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="material_adversity_index"
            name="Adversidad material"
            stroke={COLORS.urgency}
            connectNulls
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="interpersonal_risk_index"
            name="Riesgo interpersonal"
            stroke={COLORS.rumination}
            strokeWidth={2}
            connectNulls
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="relapse_context_index"
            name="Contexto de recaída"
            stroke={COLORS.craving}
            strokeDasharray="4 3"
            connectNulls
            dot={false}
          />
          <Scatter dataKey="rupture" name="Deterioro o despedida" fill={COLORS.level} shape="diamond" />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** Alerts, confirmed facts and psychosocial changes as a readable timeline. */
export function EventTimeline({ events }: { events: MetricEvent[] }) {
  if (events.length === 0) {
    return <p className="meta">Sin alertas, hechos ni cambios de contexto registrados en la ventana.</p>;
  }
  const tagLabel = (event: MetricEvent) => {
    if (event.kind === "alert") return `Alerta N${event.level}`;
    if (event.kind === "psychosocial") return "Contexto";
    return "Hecho";
  };
  return (
    <ol className="event-timeline">
      {[...events].reverse().map((event) => (
        <li key={`${event.kind}-${event.id}`} className={`event-${event.kind}`}>
          <span className="event-date">{formatDay(event.at)}</span>
          <span className={`event-tag event-tag-${event.kind}`}>{tagLabel(event)}</span>
          <span className="event-label">{event.label}</span>
          <span className="meta"> · {event.status}</span>
        </li>
      ))}
    </ol>
  );
}
