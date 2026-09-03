import { useMemo, useState } from "react";
import type {
  Agent2TraceOut,
  RiskAssessmentOut,
  RiskCalculationTrace,
  RiskRuleEvaluation,
} from "../api";

type UnknownRecord = Record<string, unknown>;

interface NormalizedRule {
  id: string;
  label: string;
  level: number | null;
  matched: boolean | null;
  selected: boolean;
  evaluated: boolean;
  condition: string | null;
  explanation: string | null;
  observed: unknown;
  threshold: unknown;
  evidence: UnknownRecord | null;
  raw: RiskRuleEvaluation;
}

const RULE_LABELS: Record<string, string> = {
  N3_senal_linguistica_ideacion_indirecta: "Posible ideación no explicitada: valoración pendiente",
  N3_convergencia_critica_extrema: "Deterioro concurrente: revisión profesional",
  N4_declaracion_ideacion_o_plan: "Declaración confirmada de ideación activa o planificación",
  N4_senal_linguistica_ideacion_directa: "Agent 2 detecta ideación directa reciente",
  N4_convergencia_critica_extrema: "Convergencia extrema de estructura, rumiación y sueño",
  N3_declaracion_crisis_consumo: "Crisis de consumo confirmada",
  N3_declaracion_recaida: "Recaída confirmada: revisión profesional, no emergencia",
  N3_senal_linguistica_crisis_consumo: "Agent 2 detecta crisis de consumo",
  N3_unstable_persistente_con_convergencia: "Inestabilidad persistente con señales convergentes",
  N3_unstable_persistente: "Banda inestable persistente",
  N2_desviacion_moderada: "Desviación moderada o inicio de inestabilidad",
  N0_estable: "Situación estable respecto a la línea base",
  N1_datos_insuficientes_o_sin_criterios: "Datos insuficientes o sin criterio superior",
  N1_sin_criterios_superiores: "No se cumplen criterios de nivel superior",
};

const LEVEL_LABELS: Record<number, string> = {
  0: "Estable",
  1: "Autogestión",
  2: "Prevención",
  3: "Revisión profesional",
  4: "Emergencia",
};

const STATUS_LABELS: Record<string, string> = {
  started: "En curso",
  succeeded: "Completada",
  refused: "Rechazada por el modelo",
  invalid_output: "Salida no válida",
  timeout: "Tiempo agotado",
  configuration_error: "Error de configuración",
  abandoned: "Interrumpida",
  rate_limited: "Límite del proveedor",
  authentication_error: "Error de autenticación",
  network_error: "Error de red",
  provider_error: "Error del proveedor",
  internal_error: "Error interno",
  failed: "Fallida",
};

const ANALYSIS_LABELS: Record<string, string> = {
  rumination_score: "Rumiación",
  negative_valence: "Valencia negativa",
  negative_valence_score: "Valencia negativa",
  urgency_score: "Urgencia",
  urgency: "Urgencia",
  urgency_level: "Nivel de urgencia",
  ideation_indirect: "Ideación indirecta",
  ideation_direct: "Ideación directa",
  consumption_crisis: "Crisis de consumo",
  ambivalence_score: "Ambivalencia",
  ambivalence: "Ambivalencia",
  emotional_complexity: "Complejidad emocional",
  emotional_complexity_score: "Complejidad emocional",
  summary: "Resumen",
  rationale: "Justificación",
  short_rationale: "Justificación breve",
};

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function firstDefined(record: UnknownRecord | null | undefined, keys: string[]): unknown {
  if (!record) return undefined;
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) return record[key];
  }
  return undefined;
}

function stringValue(record: UnknownRecord | null | undefined, keys: string[]): string | null {
  const value = firstDefined(record, keys);
  return typeof value === "string" && value.trim() ? value : null;
}

function numberValue(record: UnknownRecord | null | undefined, keys: string[]): number | null {
  const value = firstDefined(record, keys);
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function booleanValue(record: UnknownRecord | null | undefined, keys: string[]): boolean | null {
  const value = firstDefined(record, keys);
  return typeof value === "boolean" ? value : null;
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function formatNumber(value: number): string {
  if (Number.isInteger(value)) return String(value);
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Sí" : "No";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function shortId(value?: string | null): string {
  if (!value) return "—";
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function traceRecord(trace?: RiskCalculationTrace | null): UnknownRecord | null {
  return isRecord(trace) ? trace : null;
}

function rulesFromUnknown(value: unknown): RiskRuleEvaluation[] {
  if (Array.isArray(value)) return value.filter(isRecord) as RiskRuleEvaluation[];
  if (!isRecord(value)) return [];
  return Object.entries(value).map(([ruleId, detail]): RiskRuleEvaluation => {
    if (isRecord(detail)) return { rule_id: ruleId, ...detail } as RiskRuleEvaluation;
    if (typeof detail === "boolean") return { rule_id: ruleId, result: detail };
    return { rule_id: ruleId, observed: detail };
  });
}

function normalizeRules(assessment: RiskAssessmentOut): NormalizedRule[] {
  const trace = traceRecord(assessment.calculation_trace) || {};
  const source =
    firstDefined(trace, ["evaluated_rules", "rule_evaluations", "decision_path", "rules", "steps"]) ?? [];
  let rows = rulesFromUnknown(source);

  if (rows.length === 0) {
    const triggering = Array.isArray(assessment.triggering_rules)
      ? assessment.triggering_rules
      : Object.keys(assessment.triggering_rules || {});
    rows = triggering.map((rule) => ({ rule_id: String(rule), evaluated: true, matched: true }));
  }

  return rows.map((row, index) => {
    const record = row as UnknownRecord;
    const id = stringValue(record, ["rule_id", "rule", "id", "name", "code"]) || `paso-${index + 1}`;
    const matched = booleanValue(record, ["matched", "passed", "triggered", "result"]);
    const evaluated = booleanValue(record, ["evaluated", "checked"]) ?? matched !== null;
    const level = numberValue(record, ["level", "target_level", "alert_level", "risk_level"]);
    const conditions = Array.isArray(record.conditions) ? record.conditions : [];
    const conditionEvidence = Object.fromEntries(
      conditions
        .filter(isRecord)
        .map((item, conditionIndex) => [
          stringValue(item, ["label"]) || `Condición ${conditionIndex + 1}`,
          {
            observado: item.actual,
            operador: item.operator,
            esperado: item.expected,
            resultado: item.result,
          },
        ])
    );
    const evidence =
      firstDefined(record, ["evidence", "inputs", "values", "details"]) ??
      (conditions.length > 0 ? conditionEvidence : null);
    return {
      id,
      label: stringValue(record, ["label", "title", "description"]) || RULE_LABELS[id] || id,
      level,
      matched,
      selected: booleanValue(record, ["selected"]) === true,
      evaluated,
      condition: stringValue(record, ["condition", "expression", "formula"]),
      explanation: stringValue(record, ["explanation", "reason", "outcome"]),
      observed: firstDefined(record, ["observed", "actual", "value"]),
      threshold: firstDefined(record, ["threshold", "expected", "criterion"]),
      evidence: isRecord(evidence) ? evidence : null,
      raw: row,
    };
  });
}

function FieldList({ values }: { values: UnknownRecord }) {
  const entries = Object.entries(values);
  if (entries.length === 0) return <p className="meta">No hay valores registrados.</p>;
  return (
    <dl className="trace-field-list">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{ANALYSIS_LABELS[key] || key.replace(/_/g, " ")}</dt>
          <dd>{formatValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function MetricGrid({ values }: { values: UnknownRecord }) {
  const entries = Object.entries(values);
  if (entries.length === 0) return <p className="meta">No hay métricas registradas.</p>;

  return (
    <div className="metric-grid">
      {entries.map(([key, value]) => {
        const label = ANALYSIS_LABELS[key] || key.replace(/_/g, " ");
        if (typeof value === "number" && Number.isFinite(value)) {
          const isUnitScale = value >= 0 && value <= 1;
          return (
            <div className="metric-card" key={key}>
              <div className="metric-heading">
                <span>{label}</span>
                <strong>{formatNumber(value)}</strong>
              </div>
              {isUnitScale && (
                <meter aria-label={`${label}: ${formatNumber(value)}`} min={0} max={1} value={value}>
                  {value}
                </meter>
              )}
            </div>
          );
        }
        if (typeof value === "boolean") {
          return (
            <div className="metric-card" key={key}>
              <span>{label}</span>
              <strong className={value ? "boolean-yes" : "boolean-no"}>{value ? "Sí" : "No"}</strong>
            </div>
          );
        }
        return (
          <div className="metric-card metric-card-wide" key={key}>
            <span>{label}</span>
            <strong className="metric-text">{formatValue(value)}</strong>
          </div>
        );
      })}
    </div>
  );
}

function JsonDisclosure({ label, value }: { label: string; value: unknown }) {
  return (
    <details className="json-disclosure">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value ?? null, null, 2)}</pre>
    </details>
  );
}

function RulePath({ rules }: { rules: NormalizedRule[] }) {
  if (rules.length === 0) return <p className="meta">La evaluación no contiene pasos de decisión.</p>;
  return (
    <ol className="decision-path" aria-label="Ruta de reglas evaluadas">
      {rules.map((rule, index) => {
        const status = !rule.evaluated
          ? "not-evaluated"
          : rule.selected
            ? "selected"
            : rule.matched
              ? "matched"
              : "not-matched";
        const statusLabel = !rule.evaluated
          ? "Sin datos"
          : rule.selected
            ? "Regla concluyente"
            : rule.matched
              ? "Cumple, no concluye por prioridad"
              : "No cumplida";
        return (
          <li className={`decision-step ${status}`} key={`${rule.id}-${index}`}>
            <div className="decision-marker" aria-hidden="true">
              {rule.matched ? "✓" : rule.evaluated ? "×" : "·"}
            </div>
            <div className="decision-content">
              <div className="decision-heading">
                <strong>{rule.label}</strong>
                <span className={`trace-status ${status}`}>{statusLabel}</span>
              </div>
              <div className="meta">
                <code>{rule.id}</code>
                {rule.level !== null && ` · Nivel ${rule.level}`}
              </div>
              {rule.condition && <p><strong>Condición:</strong> <code>{rule.condition}</code></p>}
              {(rule.observed !== undefined || rule.threshold !== undefined) && (
                <div className="observed-grid">
                  <div><span className="meta">Valor observado</span><strong>{formatValue(rule.observed)}</strong></div>
                  <div><span className="meta">Umbral / criterio</span><strong>{formatValue(rule.threshold)}</strong></div>
                </div>
              )}
              {rule.explanation && <p>{rule.explanation}</p>}
              {rule.evidence && <FieldList values={rule.evidence} />}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function DetailedDeterministicMath({ trace }: { trace: UnknownRecord }) {
  const inputs = isRecord(trace.inputs) ? trace.inputs : {};
  const structural = isRecord(inputs.structural) ? inputs.structural : {};
  const composite = isRecord(structural.composite) ? structural.composite : {};
  const variables = Array.isArray(structural.variables) ? structural.variables.filter(isRecord) : [];
  const sleep = isRecord(inputs.sleep_trend) ? inputs.sleep_trend : {};
  const persistence = isRecord(inputs.persistence) ? inputs.persistence : {};
  const engine = isRecord(trace.engine) ? trace.engine : {};
  const thresholds = isRecord(engine.thresholds) ? engine.thresholds : {};

  if (variables.length === 0 && Object.keys(sleep).length === 0 && Object.keys(persistence).length === 0) {
    return null;
  }

  return (
    <>
      <section className="trace-section">
        <h3>Cálculo estructural completo</h3>
        <p className="meta">
          Cada z-score compara la media reciente con la línea base personal. El craving se transforma como 10 −
          craving antes de calcularlo.
        </p>
        {variables.length > 0 && (
          <div className="table-wrap">
            <table className="table trace-math-table">
              <thead>
                <tr>
                  <th>Variable</th>
                  <th>Transformación</th>
                  <th>Media baseline</th>
                  <th>Desv. poblacional</th>
                  <th>Media reciente</th>
                  <th>Diferencia</th>
                  <th>z-score</th>
                  <th>|z|</th>
                  <th>Si σ = 0</th>
                </tr>
              </thead>
              <tbody>
                {variables.map((variable, index) => (
                  <tr key={String(variable.key || index)}>
                    <td><strong>{formatValue(variable.key)}</strong></td>
                    <td>{formatValue(variable.transformation)}</td>
                    <td>{formatValue(variable.baseline_mean)}</td>
                    <td>{formatValue(variable.baseline_population_std)}</td>
                    <td>{formatValue(variable.recent_mean)}</td>
                    <td>{formatValue(variable.difference)}</td>
                    <td>{formatValue(variable.z_score)}</td>
                    <td>{formatValue(variable.absolute_z)}</td>
                    <td>{formatValue(variable.zero_std_policy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="calculation-formula-card">
          <div><span className="meta">Muestras de línea base</span><strong>{formatValue(structural.baseline_sample_count)}</strong></div>
          <div><span className="meta">Muestras recientes</span><strong>{formatValue(structural.recent_sample_count)}</strong></div>
          <div><span className="meta">Fórmula de cada z-score</span><code>{formatValue(variables[0]?.formula)}</code></div>
          <div><span className="meta">Fórmula del z compuesto</span><code>{formatValue(composite.formula)}</code></div>
          <div><span className="meta">Media de |z|</span><strong>{formatValue(composite.composite_z)}</strong></div>
          <div><span className="meta">Fórmula del score estructural</span><code>{formatValue(composite.score_formula)}</code></div>
          <div><span className="meta">Score resultante</span><strong>{formatValue(composite.score)}</strong></div>
          <div><span className="meta">Banda</span><strong>{formatValue(composite.band)}</strong></div>
        </div>
      </section>

      <div className="trace-columns">
        <section className="trace-section">
          <h3>Tendencia de sueño</h3>
          <p className="meta">Regresión lineal sobre los últimos puntos, ordenados de más antiguo a más reciente.</p>
          <FieldList values={sleep} />
        </section>
        <section className="trace-section">
          <h3>Persistencia estructural</h3>
          <p className="meta">Cuenta días naturales distintos en banda inestable.</p>
          <FieldList values={persistence} />
        </section>
      </div>

      <section className="trace-section">
        <h3>Umbrales versionados del motor</h3>
        <FieldList values={thresholds} />
      </section>
    </>
  );
}

function RiskAssessmentCard({ assessment, featured }: { assessment: RiskAssessmentOut; featured: boolean }) {
  const trace = traceRecord(assessment.calculation_trace) || {};
  const inputSignals = isRecord(assessment.input_signals) ? assessment.input_signals : {};
  const inputFacts = isRecord(assessment.input_facts) ? assessment.input_facts : {};
  const structuralScore =
    numberValue(trace, ["structural_score", "score"]) ?? numberValue(inputSignals, ["structural_score"]);
  const confidenceBand =
    stringValue(trace, ["confidence_band", "band"]) ?? stringValue(inputSignals, ["confidence_band"]);
  const zScoresValue = firstDefined(trace, ["z_scores"]) ?? inputSignals.z_scores;
  const zScores = isRecord(zScoresValue) ? zScoresValue : {};
  const traceInputs = isRecord(trace.inputs) ? trace.inputs : {};
  const structuralInput = isRecord(traceInputs.structural) ? traceInputs.structural : {};
  const structuralVersion = stringValue(structuralInput, ["calculation_version"]) || stringValue(inputSignals, ["structural_calculation_version"]) || "structural-v1";
  const meterThresholds = structuralVersion === "structural-v2"
    ? { low: 1 / 2.95, high: 1 / 2.2 }
    : structuralVersion === "structural-v1" ? { low: 0.35, high: 0.6 } : null;
  const agent2Input = isRecord(traceInputs.agent2) ? traceInputs.agent2 : {};
  const safetyReview = isRecord(agent2Input.recent_safety_review) ? agent2Input.recent_safety_review : isRecord(inputSignals.safety_review) ? inputSignals.safety_review : null;
  const safetyEvidence = Array.isArray(safetyReview?.evidence) ? safetyReview.evidence.filter(isRecord) : [];
  const safetyDriverId = stringValue(inputSignals, ["safety_driver_signal_id"]);
  const hasAgent2UsageSnapshot = Object.prototype.hasOwnProperty.call(agent2Input, "eligible_for_risk");
  const agent2WasUsed = agent2Input.eligible_for_risk === true && Boolean(assessment.linguistic_signal_id_used);
  const linguisticValue = firstDefined(agent2Input, ["values_used"]) ??
    (!hasAgent2UsageSnapshot ? inputSignals.linguistic : undefined);
  const linguistic = isRecord(linguisticValue) ? linguisticValue : {};
  const rules = normalizeRules(assessment);
  const hasFullPath = Boolean(
    trace && firstDefined(trace, ["evaluated_rules", "rule_evaluations", "decision_path", "rules", "steps"])
  );
  const conclusion = isRecord(trace.conclusion) ? trace.conclusion : {};
  const selectedRule =
    stringValue(conclusion, ["selected_rule_code"]) ||
    stringValue(trace, ["selected_rule", "stopped_at_rule", "winning_rule", "decision_rule"]) ||
    rules.find((rule) => rule.selected)?.id ||
    rules.find((rule) => rule.matched)?.id ||
    null;

  return (
    <details className={`trace-card level-${assessment.alert_level}`} open={featured}>
      <summary className="trace-card-summary">
        <span className={`risk-orb level-${assessment.alert_level}`}>L{assessment.alert_level}</span>
        <span className="trace-summary-copy">
          <strong>{LEVEL_LABELS[assessment.alert_level] || `Nivel ${assessment.alert_level}`}</strong>
          <span>{assessment.assessment_reason}</span>
        </span>
        <span className="trace-summary-meta">
          {formatDate(assessment.calculated_at)}
          <span>{assessment.model_version}</span>
        </span>
      </summary>

      <div className="trace-card-body">
        <div className="decision-overview">
          <div className="decision-result">
            <span className="meta">Conclusión determinista</span>
            <strong>Nivel {assessment.alert_level} · {LEVEL_LABELS[assessment.alert_level] || "Riesgo"}</strong>
            <p>{assessment.assessment_reason}</p>
          </div>
          <div className="score-visual">
            <span className="meta">Score estructural registrado</span>
            <strong>{structuralScore === null ? "—" : formatNumber(structuralScore)}</strong>
            {structuralScore !== null && structuralScore >= 0 && structuralScore <= 1 && meterThresholds && (
              <meter
                aria-label={`Score estructural: ${formatNumber(structuralScore)}`}
                min={0}
                max={1}
                value={structuralScore}
                low={meterThresholds.low}
                high={meterThresholds.high}
                optimum={1}
              >
                {structuralScore}
              </meter>
            )}
            <span className="meta">Banda: {confidenceBand || "—"} · {structuralVersion}</span>
          </div>
          <dl className="trace-identifiers">
            <div><dt>Regla concluyente</dt><dd><code>{selectedRule || "—"}</code></dd></div>
            <div><dt>Correlación</dt><dd title={assessment.correlation_id || undefined}><code>{shortId(assessment.correlation_id)}</code></dd></div>
            <div><dt>Traza Agent 2</dt><dd title={(assessment.agent2_trace_id || assessment.analysis_trace_id) ?? undefined}><code>{shortId(assessment.agent2_trace_id || assessment.analysis_trace_id)}</code></dd></div>
            <div><dt>Señal registrada en el ciclo</dt><dd title={assessment.linguistic_signal_id_used || undefined}><code>{shortId(assessment.linguistic_signal_id_used)}</code></dd></div>
            {safetyDriverId && <div><dt>Señal determinante de seguridad</dt><dd title={safetyDriverId}><code>{shortId(safetyDriverId)}</code></dd></div>}
          </dl>
        </div>

        {!hasFullPath && (
          <p className="trace-legacy-note">
            Esta evaluación histórica solo conserva las reglas activadas. Las evaluaciones nuevas muestran también
            las reglas descartadas, sus umbrales y valores observados.
          </p>
        )}

        <section className="trace-section" aria-labelledby={`rules-${assessment.id}`}>
          <h3 id={`rules-${assessment.id}`}>Ruta de decisión</h3>
          <p className="meta">Orden exacto registrado por el servidor. La interfaz no recalcula ni modifica el nivel.</p>
          <RulePath rules={rules} />
        </section>

        <DetailedDeterministicMath trace={trace} />

        <div className="trace-columns">
          <section className="trace-section">
            <h3>Componentes estructurales</h3>
            <MetricGrid values={zScores} />
          </section>
          <section className="trace-section">
            <h3>Análisis de Agent 2 del ciclo</h3>
            {hasAgent2UsageSnapshot && !agent2WasUsed ? (
              <p className="trace-empty">El análisis de este ciclo no aportó una señal utilizable. {safetyReview ? "Esto no descarta las señales anteriores de seguridad que figuran debajo." : "Esta evaluación histórica no incluye el desglose de señales previas de seguridad."}</p>
            ) : (
              <MetricGrid values={linguistic} />
            )}
          </section>
        </div>

        {safetyReview && (
          <section className="trace-section" aria-labelledby={`safety-${assessment.id}`}>
            <h3 id={`safety-${assessment.id}`}>Señales de seguridad revisadas</h3>
            <p className="meta">
              Ventana registrada: {formatValue(safetyReview.window_hours)} h. Un texto posterior neutro o un fallo del análisis
              no elimina estas señales previas. Son inferencias, no confirmaciones de intención o plan.
              La marca «determinante» identifica la fuente que activó la regla de seguridad; el texto original está en Evidencia.
            </p>
            {safetyEvidence.length ? <div className="table-wrap">
              <table className="table">
                <thead><tr><th scope="col">Fecha</th><th scope="col">Señal</th><th scope="col">Indicios registrados</th><th scope="col">Papel en esta decisión</th></tr></thead>
                <tbody>{safetyEvidence.map((item, index) => {
                  const signalId = stringValue(item, ["signal_id"]);
                  const isDriver = Boolean(signalId && safetyDriverId && signalId === safetyDriverId);
                  const flags = ["ideation_direct", "ideation_indirect", "consumption_crisis"].filter((key) => item[key] === true).map((key) => ANALYSIS_LABELS[key]);
                  return <tr key={signalId ?? index} className={isDriver ? "row-highlight" : undefined}>
                    <td>{formatDate(stringValue(item, ["timestamp"]))}</td>
                    <td title={signalId ?? undefined}><code>{shortId(signalId)}</code></td>
                    <td>{flags.join(" · ") || "—"}</td>
                    <td>{isDriver ? "Determinante de seguridad" : "Revisada"}</td>
                  </tr>;
                })}</tbody>
              </table>
            </div> : <p className="trace-empty">No constan señales textuales de seguridad activas en la ventana revisada. Esto no equivale a ausencia de riesgo.</p>}
          </section>
        )}

        <section className="trace-section">
          <h3>Hechos confirmados considerados</h3>
          <FieldList values={inputFacts} />
        </section>

        <div className="trace-disclosures">
          <JsonDisclosure label="Ver cálculo completo registrado" value={assessment.calculation_trace || { triggering_rules: assessment.triggering_rules }} />
          <JsonDisclosure label="Ver señales de entrada completas" value={assessment.input_signals} />
          <JsonDisclosure label="Ver hechos de entrada completos" value={assessment.input_facts || {}} />
        </div>
      </div>
    </details>
  );
}

export function RiskAssessmentTraceList({ assessments }: { assessments: RiskAssessmentOut[] }) {
  if (assessments.length === 0) {
    return <p className="info">No hay evaluaciones. Usa «Reevaluar riesgo» para crear la primera.</p>;
  }

  return (
    <div className="trace-list">
      {assessments.map((assessment, index) => (
        <RiskAssessmentCard assessment={assessment} featured={index === 0} key={assessment.id} />
      ))}
    </div>
  );
}

function getAgent2Analysis(trace: Agent2TraceOut): UnknownRecord {
  if (isRecord(trace.analysis)) return trace.analysis;
  if (isRecord(trace.output)) return trace.output;
  return {};
}

function traceTimestamp(trace: Agent2TraceOut): string | null {
  return trace.completed_at || trace.started_at || trace.created_at || null;
}

function agent2ResponseModel(trace: Agent2TraceOut): string | null {
  return trace.response_model || trace.model || null;
}

function statusClass(status: string): string {
  return status === "succeeded" ? "success" : status === "started" ? "pending" : "failure";
}

function Agent2TraceCard({ trace, assessment }: { trace: Agent2TraceOut; assessment?: RiskAssessmentOut }) {
  const analysis = getAgent2Analysis(trace);
  const tokens = [
    trace.input_tokens != null ? `${trace.input_tokens} entrada` : null,
    trace.output_tokens != null ? `${trace.output_tokens} salida` : null,
  ].filter(Boolean).join(" · ");

  return (
    <details className={`agent2-card ${statusClass(trace.status)}`}>
      <summary className="agent2-summary">
        <span className={`trace-status ${statusClass(trace.status)}`}>
          {STATUS_LABELS[trace.status] || trace.status}
        </span>
        <span className="trace-summary-copy">
          <strong>{agent2ResponseModel(trace) || trace.requested_model || "Modelo no registrado"}</strong>
          <span>{formatDate(traceTimestamp(trace))}</span>
        </span>
        <span className="trace-summary-meta">
          {trace.latency_ms != null ? `${trace.latency_ms.toLocaleString()} ms` : "Latencia —"}
          <span>{tokens || "Tokens —"}</span>
        </span>
      </summary>

      <div className="trace-card-body">
        <div className="agent2-linkage" aria-label="Vínculos de trazabilidad">
          <div><span>Origen</span><strong>{trace.source_type === "chat_message" ? "Chat" : trace.source_type === "diary_entry" ? "Diario" : trace.source_type || "—"}</strong></div>
          <div><span>ID del origen</span><code title={trace.source_id || undefined}>{shortId(trace.source_id)}</code></div>
          <div><span>Correlación</span><code title={trace.correlation_id || undefined}>{shortId(trace.correlation_id)}</code></div>
          <div><span>Señal guardada</span><code title={trace.signal_id || undefined}>{shortId(trace.signal_id)}</code></div>
          <div><span>Evaluación del mismo ciclo</span><code title={(trace.risk_assessment_id || assessment?.id) ?? undefined}>{shortId(trace.risk_assessment_id || assessment?.id)}</code></div>
          <div><span>Utilizada por el motor</span><strong>{trace.used_by_risk_engine ? "Sí" : "No"}</strong></div>
          <div><span>Petición proveedor</span><code title={trace.provider_request_id || undefined}>{shortId(trace.provider_request_id)}</code></div>
        </div>

        <div className="agent2-conversation">
          <section className="agent2-panel agent2-input">
            <h3>Texto analizado</h3>
            <p className="meta">Input textual exacto remitido a Agent 2.</p>
            {trace.source_text ? (
              <blockquote>{trace.source_text}</blockquote>
            ) : (
              <p className="trace-empty">El texto no está disponible en esta traza histórica.</p>
            )}
          </section>
          <section className="agent2-panel agent2-output">
            <h3>Respuesta estructurada</h3>
            <p className="meta">Inferencia del modelo; no equivale a un hecho confirmado ni fija el nivel.</p>
            {Object.keys(analysis).length > 0 ? (
              <MetricGrid values={analysis} />
            ) : (
              <p className="trace-empty">
                {trace.status === "succeeded" ? "No hay salida estructurada registrada." : "La llamada no produjo una salida válida."}
              </p>
            )}
          </section>
        </div>

        {(trace.error_kind || trace.error_code || trace.http_status) && (
          <p className="trace-error">
            <strong>Error sanitizado:</strong>{" "}
            <code>{[trace.error_kind, trace.error_code, trace.http_status ? `HTTP ${trace.http_status}` : null].filter(Boolean).join(" · ")}</code>
          </p>
        )}

        <dl className="trace-run-metadata">
          <div><dt>Proveedor</dt><dd>{trace.provider || "—"}</dd></div>
          {/* Only meaningful for a self-hosted endpoint, and then essential:
              two installs can both report "llama-3.1-8b" and mean different
              weights on different machines. */}
          <div>
            <dt>Endpoint</dt>
            <dd>{trace.provider_base_url ? <code>{trace.provider_base_url}</code> : "API oficial"}</dd>
          </div>
          <div><dt>Modelo solicitado</dt><dd>{trace.requested_model || "—"}</dd></div>
          <div><dt>Modelo respondido</dt><dd>{agent2ResponseModel(trace) || "—"}</dd></div>
          <div><dt>Esfuerzo</dt><dd>{trace.effort || "—"}</dd></div>
          <div><dt>Máximo de tokens</dt><dd>{trace.max_tokens ?? "—"}</dd></div>
          <div><dt>Inicio</dt><dd>{formatDate(trace.started_at || trace.created_at)}</dd></div>
          <div><dt>Fin</dt><dd>{formatDate(trace.completed_at)}</dd></div>
          <div><dt>Fin del modelo</dt><dd>{trace.stop_reason || "—"}</dd></div>
          <div><dt>ID de mensaje proveedor</dt><dd title={trace.provider_message_id || undefined}><code>{shortId(trace.provider_message_id)}</code></dd></div>
          <div><dt>ID de petición proveedor</dt><dd title={trace.provider_request_id || undefined}><code>{shortId(trace.provider_request_id)}</code></dd></div>
          <div><dt>Tokens de entrada</dt><dd>{trace.input_tokens ?? "—"}</dd></div>
          <div><dt>Tokens de salida</dt><dd>{trace.output_tokens ?? "—"}</dd></div>
          <div><dt>Tokens creados en caché</dt><dd>{trace.cache_creation_input_tokens ?? "—"}</dd></div>
          <div><dt>Tokens leídos de caché</dt><dd>{trace.cache_read_input_tokens ?? "—"}</dd></div>
          <div><dt>Versión del prompt</dt><dd>{trace.prompt_version || "—"}</dd></div>
          <div><dt>SHA-256 del prompt estático</dt><dd title={trace.prompt_sha256 || undefined}><code>{shortId(trace.prompt_sha256)}</code></dd></div>
          <div><dt>Versión del esquema</dt><dd>{trace.schema_version || "—"}</dd></div>
          <div><dt>SHA-256 del esquema</dt><dd title={trace.schema_sha256 || undefined}><code>{shortId(trace.schema_sha256)}</code></dd></div>
          <div><dt>Release</dt><dd title={trace.app_release || undefined}>{shortId(trace.app_release)}</dd></div>
        </dl>

        <JsonDisclosure label="Ver salida JSON exacta" value={analysis} />
      </div>
    </details>
  );
}

export function Agent2TraceList({
  traces,
  assessments,
  legacySignalCount = 0,
}: {
  traces: Agent2TraceOut[];
  assessments: RiskAssessmentOut[];
  legacySignalCount?: number;
}) {
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");

  const statuses = useMemo(() => Array.from(new Set(traces.map((trace) => trace.status))).sort(), [traces]);
  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return traces.filter((trace) => {
      if (status !== "all" && trace.status !== status) return false;
      if (!normalizedQuery) return true;
      return [trace.correlation_id, trace.response_model, trace.model, trace.requested_model, trace.source_text, trace.provider]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(normalizedQuery));
    });
  }, [query, status, traces]);

  function linkedAssessment(trace: Agent2TraceOut): RiskAssessmentOut | undefined {
    if (trace.risk_assessment_id) return assessments.find((item) => item.id === trace.risk_assessment_id);
    if (trace.correlation_id) return assessments.find((item) => item.correlation_id === trace.correlation_id);
    return undefined;
  }

  return (
    <>
      <div className="trace-filters" role="search" aria-label="Filtros de trazas de Agent 2">
        <label>
          Buscar trazas
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Correlación, modelo o texto…"
          />
        </label>
        <label>
          Estado
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">Todos</option>
            {statuses.map((item) => (
              <option value={item} key={item}>{STATUS_LABELS[item] || item}</option>
            ))}
          </select>
        </label>
        <div className="trace-filter-count" aria-live="polite">
          {filtered.length} de {traces.length} llamadas
        </div>
      </div>

      {traces.length === 0 && (
        <p className="info">
          Aún no hay llamadas de Agent 2 con trazabilidad completa.
        </p>
      )}
      {legacySignalCount > 0 && (
        <p className="info">
          Hay {legacySignalCount} señal(es) lingüística(s) histórica(s) anterior(es) al nuevo tracking; no incluyen
          texto de origen, metadatos del proveedor ni vínculo de correlación reconstruible.
        </p>
      )}
      {traces.length > 0 && filtered.length === 0 && (
        <p className="info">Ninguna llamada coincide con los filtros.</p>
      )}
      <div className="trace-list">
        {filtered.map((trace) => (
          <Agent2TraceCard trace={trace} assessment={linkedAssessment(trace)} key={trace.id} />
        ))}
      </div>
    </>
  );
}
