/**
 * Narrative cards for the professional panel.
 *
 * These render what the server already decided into sentences a clinician
 * can act on. Nothing here recomputes anything, and nothing here calls a
 * model: the text comes from the deterministic explanation built alongside
 * the risk decision itself, so an old assessment always explains itself
 * with the data it was actually decided on.
 */
import { ReactNode, useState } from "react";
import {
  DRIVER_FAMILY_SHORT,
  EvidenceItemOut,
  EvidenceRef,
  LevelExplanationOut,
  StructuralExplanationOut,
  formatDateTime,
} from "../api";

function EvidenceBlock({ evidence, title }: { evidence?: EvidenceRef | null; title?: string }) {
  if (!evidence) return null;
  const isText = evidence.kind === "texto";
  const isFact = evidence.kind === "hecho";
  if (!isText && !isFact) {
    return (
      <div className="evidence-block evidence-structural">
        <div className="evidence-head">
          <strong>{title || "Evidencia"}</strong>
          <span className="meta">Check-ins diarios — ver gráficas de esta ficha</span>
        </div>
      </div>
    );
  }
  return (
    <div className={`evidence-block ${isFact ? "evidence-fact" : "evidence-text"}`}>
      <div className="evidence-head">
        <strong>{title || (isFact ? "Hecho declarado" : "Texto original del paciente")}</strong>
        <span className="meta">
          {evidence.source_label} · {formatDateTime(evidence.created_at)}
          {evidence.category ? ` · ${evidence.category}` : ""}
          {evidence.declared_by ? ` · declarado por ${evidence.declared_by}` : ""}
        </span>
      </div>
      <blockquote className="evidence-quote">{evidence.text || evidence.excerpt || "—"}</blockquote>
    </div>
  );
}

/** "Why is this patient at this level right now" — the headline card. */
export function LevelExplanationCard({
  explanation,
  actions,
}: {
  explanation: LevelExplanationOut;
  actions?: ReactNode;
}) {
  const level = explanation.level;
  return (
    <section className={`explain-card level-${level ?? "na"}`}>
      <div className="explain-head">
        <div>
          <div className="explain-level">{explanation.level_label}</div>
          <p className="explain-headline">{explanation.headline}</p>
        </div>
        <div className="explain-driver">
          <span className="meta">Lo ha disparado</span>
          <strong>{DRIVER_FAMILY_SHORT[explanation.driver_family] || explanation.driver_family_label}</strong>
          <span className="meta">{formatDateTime(explanation.calculated_at)}</span>
        </div>
        {actions && <div className="explain-actions">{actions}</div>}
      </div>

      <dl className="explain-grid">
        <div>
          <dt>Qué significa este nivel</dt>
          <dd>{explanation.level_meaning}</dd>
        </div>
        {explanation.rule_title && (
          <div>
            <dt>Regla concreta</dt>
            <dd>
              {explanation.rule_title}
              {explanation.rule_explanation ? ` — ${explanation.rule_explanation}` : ""}
            </dd>
          </div>
        )}
        {explanation.driver_evidence_kind && (
          <div>
            <dt>Qué tipo de evidencia es</dt>
            <dd>{explanation.driver_evidence_kind}</dd>
          </div>
        )}
        {explanation.what_now && (
          <div>
            <dt>Qué hacer ahora</dt>
            <dd>{explanation.what_now}</dd>
          </div>
        )}
      </dl>

      {explanation.structural_reconciliation && (
        <p className="explain-reconcile">{explanation.structural_reconciliation}</p>
      )}

      <EvidenceBlock evidence={explanation.driver_evidence} title="Evidencia que disparó este nivel" />

      {explanation.rule_code && (
        <p className="meta explain-code">
          Regla del motor: <code>{explanation.rule_code}</code>
          {explanation.assessment_id ? ` · evaluación ${explanation.assessment_id.slice(0, 8)}` : ""}
        </p>
      )}
    </section>
  );
}

const DIRECTION_LABEL: Record<string, string> = {
  cambio: "Cambio de sueño: valorar contexto",
  peor: "Peor que su línea base",
  mejor: "Mejor que su línea base",
  igual: "Sin cambio apreciable",
  sin_datos: "Sin datos",
};

/** The structural score, explained and decomposed by variable. */
export function StructuralExplanationCard({ explanation }: { explanation: StructuralExplanationOut }) {
  return (
    <section className="explain-card explain-structural">
      <h3>Score estructural, explicado</h3>
      <p className="explain-headline">{explanation.summary}</p>
      <p className="explain-scale">{explanation.scale_note}</p>
      {explanation.band_meaning && (
        <p className="meta">
          Banda actual <strong>{explanation.band_label}</strong>: {explanation.band_meaning}
        </p>
      )}
      {explanation.direction_summary && <p className="explain-direction">{explanation.direction_summary}</p>}

      {explanation.variables.length > 0 && (
        <div className="table-wrap">
          <table className="table explain-table">
            <thead>
              <tr>
                <th>Variable</th>
                <th>Su media base</th>
                <th>Últimos 7 días</th>
                <th>Diferencia</th>
                <th>z</th>
                <th>Lectura</th>
              </tr>
            </thead>
            <tbody>
              {explanation.variables.map((row) => (
                <tr key={row.key} className={`direction-${row.direction}`}>
                  <td>
                    <strong>{row.label}</strong>
                    {row.note && <div className="meta">{row.note}</div>}
                  </td>
                  <td>{row.baseline_mean != null ? row.baseline_mean.toFixed(2) : "—"}</td>
                  <td>{row.recent_mean != null ? row.recent_mean.toFixed(2) : "—"}</td>
                  <td>{row.difference != null ? row.difference.toFixed(2) : "—"}</td>
                  <td>{row.z_score != null ? row.z_score.toFixed(2) : "—"}</td>
                  <td>
                    <span className={`direction-pill direction-${row.direction}`}>
                      {DIRECTION_LABEL[row.direction] || row.direction}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <dl className="explain-grid">
        <div>
          <dt>Desviación adversa media</dt>
          <dd>{explanation.adverse_composite_z?.toFixed(2) ?? "—"}{explanation.calculation_version === "structural-v2" ? " (media de los cuatro ejes, sin compensar con mejoras; sueño bilateral)" : " (cálculo histórico: media del subconjunto adverso)"}</dd>
        </div>
        <div>
          <dt>Desviación favorable media</dt>
          <dd>{explanation.favourable_composite_z?.toFixed(2) ?? "—"}{explanation.calculation_version === "structural-v2" ? " (cuatro ejes; el sueño no se clasifica automáticamente como mejora)" : " (cálculo histórico: media del subconjunto favorable)"}</dd>
        </div>
        <div>
          <dt>Muestras usadas</dt>
          <dd>
            {explanation.baseline_sample_count ?? "—"} check-ins de base (21 días) ·{" "}
            {explanation.recent_sample_count ?? "—"} recientes (7 días)
          </dd>
        </div>
        <div>
          <dt>Tendencia de sueño</dt>
          <dd>
            {explanation.sleep_trend || "—"}
            {explanation.sleep_trend_slope != null
              ? ` (pendiente ${explanation.sleep_trend_slope.toFixed(3)} h/registro)`
              : ""}
          </dd>
        </div>
      </dl>

      {explanation.calculation_version === "structural-v2" && (
        <p className="explain-reconcile">
          Componente de deterioro usado por las reglas: <strong>{explanation.deterioration_score?.toFixed(3) ?? "no evaluable"}</strong>
          {explanation.deterioration_band ? ` · ${explanation.deterioration_band}` : ""}. Es distinto de la similitud:
          las mejoras no compensan los cambios adversos. Un cambio de sueño en cualquier dirección requiere contexto clínico.
        </p>
      )}

      {explanation.caveats.length > 0 && (
        <ul className="explain-caveats">
          {explanation.caveats.map((caveat) => (
            <li key={caveat}>{caveat}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The evidence feed: one card per analysed text, showing what the patient
 * wrote, what the model read in it, and what the engine did next.
 */
export function EvidenceFeed({
  items,
  highlightId,
}: {
  items: EvidenceItemOut[];
  highlightId?: string | null;
}) {
  const [filter, setFilter] = useState<"all" | "chat_message" | "diary_entry" | "flagged">("all");
  const filtered = items.filter((item) => {
    if (filter === "all") return true;
    if (filter === "flagged") return item.flags.length > 0;
    return item.source_type === filter;
  });

  return (
    <div className="evidence-feed">
      <div className="filters">
        {(
          [
            ["all", `Todo (${items.length})`],
            ["chat_message", `Chat (${items.filter((i) => i.source_type === "chat_message").length})`],
            ["diary_entry", `Diario (${items.filter((i) => i.source_type === "diary_entry").length})`],
            ["flagged", `Con bandera (${items.filter((i) => i.flags.length > 0).length})`],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={filter === value ? "tab active" : "tab"}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>

      {filtered.length === 0 && (
        <p className="meta">
          No hay textos analizados con este filtro. El Agente 2 solo analiza mensajes de chat y entradas de
          diario escritas por el paciente.
        </p>
      )}

      {filtered.map((item) => (
        <article
          key={item.trace_id}
          id={`evidence-${item.trace_id}`}
          className={`evidence-item${highlightId === item.trace_id ? " evidence-highlight" : ""}${
            item.flags.length > 0 ? " evidence-flagged" : ""
          }`}
        >
          <header className="evidence-item-head">
            <span className={`source-pill source-${item.source_type}`}>{item.source_label}</span>
            <span className="meta">{formatDateTime(item.source_created_at || item.analysed_at)}</span>
            {item.resulting_level != null && (
              <span className={`level-pill level-${item.resulting_level}`}>N{item.resulting_level}</span>
            )}
            {item.alert_id && (
              <span className="evidence-alert-tag">
                Generó alerta N{item.alert_level} ({item.alert_status})
              </span>
            )}
            {item.status !== "succeeded" && <span className="evidence-status">análisis: {item.status}</span>}
          </header>

          <blockquote className="evidence-quote">{item.source_text || "(texto no disponible)"}</blockquote>

          <p className="evidence-reading">
            <strong>Lectura del Agente 2:</strong> {item.reading}
            {item.short_rationale ? ` «${item.short_rationale}»` : ""}
          </p>

          {item.analysis && (
            <div className="evidence-metrics">
              {(
                [
                  ["Rumiación", "rumination_score"],
                  ["Valencia negativa", "negative_valence"],
                  ["Urgencia", "urgency_level"],
                  ["Ambivalencia", "ambivalence"],
                ] as const
              ).map(([label, key]) => {
                const value = item.analysis?.[key];
                return (
                  <span key={key} className="evidence-metric">
                    {label}: <strong>{typeof value === "number" ? value.toFixed(2) : "—"}</strong>
                  </span>
                );
              })}
              {item.flags.map((flag) => (
                <span key={flag} className="evidence-flag">
                  {flag}
                </span>
              ))}
            </div>
          )}

          <p className="meta">
            {item.used_by_risk_engine
              ? "Esta señal fue la que consumió el motor de riesgo en esa evaluación."
              : "El motor de riesgo no usó esta señal para decidir el nivel (fuera de ventana de 12 h o superada por otra regla)."}
            {item.resulting_rule ? ` · regla: ${item.resulting_rule}` : ""}
          </p>
        </article>
      ))}
    </div>
  );
}
