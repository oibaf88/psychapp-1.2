/**
 * The patient's social context, as the therapist needs to read it.
 *
 * Two design rules drive everything here:
 *
 *   1. Every reading shows the sentence it came from. A domain card without
 *      its quote is an unfalsifiable claim about someone's life, and the
 *      therapist has to be able to disagree with a specific row.
 *   2. Inference and declaration never look the same. Agent 4's readings are
 *      labelled as inferences and can be confirmed or dismissed; what a
 *      professional recorded is marked as a declaration and outranks them.
 */
import { FormEvent, useState } from "react";
import {
  PSYCHOSOCIAL_DIRECTION_OPTIONS,
  PSYCHOSOCIAL_DOMAIN_OPTIONS,
  PSYCHOSOCIAL_STATE_OPTIONS,
  PSYCHOSOCIAL_STATE_TONE,
  PsychosocialDomainOut,
  PsychosocialIndexOut,
  PsychosocialViewOut,
  api,
  formatDateTime,
} from "../api";

function IndexMeter({ index }: { index: PsychosocialIndexOut }) {
  const value = index.value;
  const pct = value === null || value === undefined ? 0 : Math.round(value * 100);
  return (
    <div className={`ps-index ps-index-${index.band}`}>
      <div className="ps-index-head">
        <strong>{index.label}</strong>
        <span className="ps-index-value">{value === null || value === undefined ? "sin datos" : value.toFixed(2)}</span>
      </div>
      <div
        className="ps-index-bar"
        role="img"
        aria-label={`${index.label}: ${value === null || value === undefined ? "sin datos" : value.toFixed(2)} sobre 1`}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      <p className="meta">
        {index.direction === "higher_is_better" ? "Más alto es mejor." : "Más alto es peor."} {index.meaning}
      </p>
      <p className="meta">{index.threshold_note}</p>
    </div>
  );
}

function DomainCard({
  domain,
  canEdit,
  busy,
  onConfirm,
  onDismiss,
}: {
  domain: PsychosocialDomainOut;
  canEdit: boolean;
  busy: boolean;
  onConfirm: (domain: PsychosocialDomainOut) => void;
  onDismiss: (domain: PsychosocialDomainOut) => void;
}) {
  const tone = PSYCHOSOCIAL_STATE_TONE[domain.state] ?? "neutral";
  return (
    <article className={`ps-domain ps-tone-${tone}`}>
      <header className="ps-domain-head">
        <h4>{domain.label}</h4>
        <span className={`ps-chip ps-chip-${tone}`}>{domain.state_label}</span>
        {domain.direction !== "desconocido" && (
          <span className="ps-chip ps-chip-plain">{domain.direction_label}</span>
        )}
        {domain.is_recent_change && <span className="ps-chip ps-chip-new">últimos 14 días</span>}
        {domain.is_declared && <span className="ps-chip ps-chip-fact">declaración</span>}
        {domain.is_stale && <span className="ps-chip ps-chip-plain">sin actualizar</span>}
        {!domain.counts_for_scoring && (
          <span className="ps-chip ps-chip-plain" title="Confianza por debajo de 0.50: no entra en los índices">
            no puntúa
          </span>
        )}
      </header>

      <p>{domain.summary}</p>

      {domain.evidence_quote && (
        <blockquote className="evidence-quote">
          «{domain.evidence_quote}»
          <span className="meta">
            {" "}
            — {domain.source_type === "professional" ? "registrado en consulta" : "texto del paciente"},{" "}
            {formatDateTime(domain.observed_at)}
          </span>
        </blockquote>
      )}

      <p className="meta">
        {domain.evidence_kind} · confianza {domain.confidence.toFixed(2)} · {domain.onset_label}
      </p>

      {domain.has_pending_update && (
        <p className="info">
          Hay una lectura más reciente del Agente 4 para este dominio que contradice lo que confirmaste. No se
          ha aplicado sola: revísala en el historial y confírmala si procede.
        </p>
      )}

      {domain.session_question && (
        <p className="ps-question">
          <strong>Para preguntar:</strong> {domain.session_question}
        </p>
      )}

      {canEdit && domain.observation_id && (
        <div className="ps-domain-actions">
          {!domain.is_declared && (
            <button type="button" disabled={busy} onClick={() => onConfirm(domain)}>
              Confirmar como hecho
            </button>
          )}
          <button type="button" className="secondary" disabled={busy} onClick={() => onDismiss(domain)}>
            Descartar lectura
          </button>
        </div>
      )}
    </article>
  );
}

export default function PsychosocialPanel({
  patientId,
  view,
  canEdit,
  onChanged,
}: {
  patientId: string;
  view: PsychosocialViewOut;
  canEdit: boolean;
  onChanged: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    domain: PSYCHOSOCIAL_DOMAIN_OPTIONS[0].value,
    state: "riesgo_moderado",
    direction: "desconocido",
    summary: "",
  });

  async function run(action: () => Promise<unknown>, successMessage: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(successMessage);
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function confirmDomain(domain: PsychosocialDomainOut) {
    void run(
      () =>
        api.post(
          `/api/v1/professional/patients/${patientId}/psychosocial/${domain.observation_id}/confirm`
        ),
      `«${domain.label}» pasa a ser un hecho confirmado. El motor se ha reevaluado.`
    );
  }

  function dismissDomain(domain: PsychosocialDomainOut) {
    const reason = window.prompt(
      `¿Por qué descartas la lectura de «${domain.label}»? Queda registrado en el historial.`,
      "Falso positivo confirmado con el paciente en sesión"
    );
    if (!reason) return;
    void run(
      () =>
        api.post(
          `/api/v1/professional/patients/${patientId}/psychosocial/${domain.observation_id}/dismiss`,
          { reason }
        ),
      `Lectura de «${domain.label}» descartada y motor reevaluado sin ella.`
    );
  }

  function submitObservation(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      await api.post(`/api/v1/professional/patients/${patientId}/psychosocial`, form);
      setForm({ ...form, summary: "" });
      setShowForm(false);
    }, "Observación registrada como declaración profesional. El motor se ha reevaluado.");
  }

  return (
    <div className="ps-panel">
      <p className="subtitle">{view.what_this_is}</p>
      <p className="ps-headline">{view.headline}</p>
      {message && <p className="info">{message}</p>}
      {error && <p className="error">{error}</p>}

      {view.leave_taking && (
        <div className="alert-snippet alert-level-4">
          <strong>Señal de despedida vigente</strong>
          <p>{String(view.leave_taking.summary ?? "")}</p>
          {view.leave_taking.evidence_quote && (
            <blockquote className="evidence-quote">«{String(view.leave_taking.evidence_quote)}»</blockquote>
          )}
          <p className="meta">
            Repartir pertenencias, dejar asuntos en orden o despedirse son señales que por separado parecen
            inocuas. Valóralas junto al resto de esta ficha, no de forma aislada.
          </p>
        </div>
      )}

      <section className="ps-indices">
        {view.indices.map((index) => (
          <IndexMeter key={index.key} index={index} />
        ))}
      </section>

      {view.session_questions.length > 0 && (
        <section className="card ps-session">
          <h3>Qué mirar en la próxima sesión</h3>
          <p className="subtitle">
            Generado a partir de lo que se está moviendo, no de un cuestionario fijo. Cada pregunta lleva el
            motivo por el que aparece.
          </p>
          <ol className="ps-question-list">
            {view.session_questions.map((item) => (
              <li key={item.domain}>
                <strong>{item.label}.</strong> {item.question}
                <div className="meta">
                  Porque: {item.because}
                  {item.quote ? ` · «${item.quote}»` : ""}
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      {!view.available && (
        <p className="meta">
          Cuando el paciente escriba en el chat o el diario sobre dónde vive, con quién, cómo va de dinero o
          con quién cuenta, el Agente 4 lo estructurará aquí automáticamente. También puedes registrarlo tú.
        </p>
      )}

      {view.groups.map((group) => (
        <section key={group.group} className="ps-group">
          <h3>{group.group_label}</h3>
          <div className="ps-domain-grid">
            {group.domains.map((domain) => (
              <DomainCard
                key={domain.domain}
                domain={domain}
                canEdit={canEdit}
                busy={busy}
                onConfirm={confirmDomain}
                onDismiss={dismissDomain}
              />
            ))}
          </div>
        </section>
      ))}

      <p className="meta">
        {view.known_domain_count} de {view.total_domain_count} dominios con información.
        {view.stale_domains.length > 0 && (
          <> Sin actualizar desde hace meses: {view.stale_domains.map((d) => d.label).join(", ")}.</>
        )}
      </p>

      {canEdit && (
        <section className="card">
          <h3>Registrar contexto que el paciente no ha escrito</h3>
          <p className="subtitle">
            Lo que registres aquí es una <strong>declaración profesional</strong>: manda sobre la lectura del
            Agente 4 en ese dominio y entra en el motor determinista igual que el resto.
          </p>
          {!showForm && (
            <button type="button" onClick={() => setShowForm(true)}>
              Añadir observación
            </button>
          )}
          {showForm && (
            <form onSubmit={submitObservation} className="stack-form">
              <label>
                Dominio
                <select value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })}>
                  {PSYCHOSOCIAL_DOMAIN_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Situación
                <select value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })}>
                  {PSYCHOSOCIAL_STATE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Evolución
                <select
                  value={form.direction}
                  onChange={(e) => setForm({ ...form, direction: e.target.value })}
                >
                  {PSYCHOSOCIAL_DIRECTION_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Descripción
                <textarea
                  required
                  rows={2}
                  value={form.summary}
                  onChange={(e) => setForm({ ...form, summary: e.target.value })}
                  placeholder="Ej.: pierde la plaza en el piso tutelado el día 30; sin alternativa todavía."
                />
              </label>
              <div className="ps-domain-actions">
                <button type="submit" disabled={busy}>
                  Registrar y reevaluar
                </button>
                <button type="button" className="secondary" onClick={() => setShowForm(false)}>
                  Cancelar
                </button>
              </div>
            </form>
          )}
        </section>
      )}

      <section className="card">
        <div className="ps-domain-actions">
          <h3 style={{ margin: 0 }}>Historial de observaciones ({view.history.length})</h3>
          <button type="button" className="secondary" onClick={() => setShowHistory((value) => !value)}>
            {showHistory ? "Ocultar" : "Mostrar"}
          </button>
        </div>
        {showHistory && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Dominio</th>
                  <th>Situación</th>
                  <th>Origen</th>
                  <th>Estado</th>
                  <th>Cita</th>
                </tr>
              </thead>
              <tbody>
                {view.history.map((item) => (
                  <tr key={item.observation_id}>
                    <td className="meta">{formatDateTime(item.observed_at)}</td>
                    <td>{item.label}</td>
                    <td>{item.state_label}</td>
                    <td className="meta">
                      {item.recorded_by === "professional" ? "Profesional" : "Agente 4"}
                    </td>
                    <td className="meta">
                      {item.dismissed_at
                        ? `Descartada: ${item.dismissed_reason ?? "sin motivo"}`
                        : item.is_confirmed
                        ? "Confirmada"
                        : item.is_current
                        ? "Vigente"
                        : "Sustituida"}
                    </td>
                    <td>{item.evidence_quote ? `«${item.evidence_quote}»` : "—"}</td>
                  </tr>
                ))}
                {view.history.length === 0 && (
                  <tr>
                    <td colSpan={6}>Sin observaciones registradas.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
