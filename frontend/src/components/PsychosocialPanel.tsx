/**
 * The patient's social context, as extracted from what they wrote.
 *
 * Housing, who they live with, support, family, money, work, losses,
 * engagement with treatment, exposure to using environments. This is the
 * part of a person's situation that usually moves *before* their mood
 * does, and the previous version of the app discarded all of it.
 *
 * Every card carries the literal sentence the observation came from,
 * because these are model inferences and the therapist has to be able to
 * check them. Confirming or refuting is a human act that outranks the
 * model: confirmed counts at full weight in the deterministic index,
 * refuted stops counting entirely.
 */
import { useState } from "react";
import {
  PsychosocialDomainOut,
  PsychosocialExplanationOut,
  PsychosocialIndexReadingOut,
  api,
  formatDateTime,
} from "../api";

const VALENCE_LABEL: Record<string, string> = {
  risk: "Adverso",
  protective: "Protector",
  neutral: "Descriptivo",
};

const STATUS_LABEL: Record<string, string> = {
  inferred: "Inferido por el Agente 4",
  confirmed: "Confirmado por ti",
  refuted: "Refutado — no cuenta",
};

function IndexGauge({ index, band }: { index?: number | null; band: string }) {
  const pct = index == null ? 0 : Math.round(index * 100);
  return (
    <div className={`psy-gauge psy-band-${band}`}>
      <div className="psy-gauge-value">{index == null ? "—" : index.toFixed(2)}</div>
      <div className="psy-gauge-track" aria-hidden="true">
        <div className="psy-gauge-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="psy-gauge-scale">
        <span>0.00 sin adversidad</span>
        <span>1.00 adversidad marcada</span>
      </div>
    </div>
  );
}

/**
 * The four deterministic indices.
 *
 * Deliberately four numbers rather than one: losing your flat and feeling
 * like a burden to your family are both "adversity", but they call for
 * different responses, and a single blended figure lets one mask the other.
 * Each is rendered with the threshold it is compared against, because a
 * number a therapist cannot locate on a scale is a number they cannot argue
 * with. "Sin datos" is a reading in its own right — it is not "bien".
 */
function IndexReadings({ readings }: { readings: PsychosocialIndexReadingOut[] }) {
  if (readings.length === 0) return null;
  return (
    <div className="psy-index-grid">
      {readings.map((reading) => (
        <div key={reading.key} className={`psy-index-tile psy-index-${reading.state}`}>
          <div className="psy-index-head">
            <span className="psy-index-label">{reading.label}</span>
            <span className="psy-index-value">
              {reading.value == null ? "sin datos" : reading.value.toFixed(2)}
            </span>
          </div>
          {reading.value != null && (
            <div className="psy-index-track" aria-hidden="true">
              <div className="psy-index-fill" style={{ width: `${Math.round(reading.value * 100)}%` }} />
              <div className="psy-index-mark" style={{ left: `${Math.round(reading.threshold * 100)}%` }} />
            </div>
          )}
          <div className="psy-index-threshold">{reading.threshold_label}</div>
          <p className="psy-index-meaning">{reading.meaning}</p>
          <p className="psy-index-note">{reading.note}</p>
        </div>
      ))}
    </div>
  );
}

function DomainCard({
  domain,
  onAdjudicate,
  busy,
  canAdjudicate,
}: {
  domain: PsychosocialDomainOut;
  onAdjudicate: (id: string, status: "confirmed" | "refuted" | "inferred") => void;
  busy: boolean;
  canAdjudicate: boolean;
}) {
  return (
    <article className={`psy-card psy-${domain.valence} psy-status-${domain.status}`}>
      <header className="psy-card-head">
        <div>
          <strong>{domain.label}</strong>
          <div className="psy-category">{domain.category_label}</div>
        </div>
        <div className="psy-tags">
          <span className={`psy-pill psy-pill-${domain.valence}`}>{VALENCE_LABEL[domain.valence]}</span>
          {domain.is_change && <span className="psy-pill psy-pill-change">Cambio reciente</span>}
          {!domain.counts_for_scoring && (
            <span className="psy-pill psy-pill-unscored" title="Confianza por debajo del umbral de puntuación">
              No puntúa
            </span>
          )}
          {domain.is_stale && (
            <span className="psy-pill psy-pill-stale" title="Sin novedades desde hace meses">
              Antigua
            </span>
          )}
          {domain.has_pending_update && (
            <span className="psy-pill psy-pill-pending" title="Lectura nueva sin revisar">
              Actualización pendiente
            </span>
          )}
          {domain.status !== "inferred" && (
            <span className={`psy-pill psy-pill-${domain.status}`}>{STATUS_LABEL[domain.status]}</span>
          )}
        </div>
      </header>

      <p className="psy-summary">{domain.summary}</p>

      {domain.quote && (
        <blockquote className="evidence-quote">
          <span className="meta">Sus palabras · {formatDateTime(domain.observed_at)}</span>
          <br />
          {domain.quote}
        </blockquote>
      )}

      <dl className="psy-numbers">
        <div>
          <dt>Intensidad</dt>
          <dd>{domain.intensity.toFixed(2)}</dd>
        </div>
        <div>
          <dt>Confianza del modelo</dt>
          <dd>{domain.confidence.toFixed(2)}</dd>
        </div>
        <div>
          <dt>Peso del dominio</dt>
          <dd>{domain.weight.toFixed(2)}</dd>
        </div>
        <div>
          <dt>Aporta al índice</dt>
          <dd>{domain.contribution.toFixed(2)}</dd>
        </div>
      </dl>

      {domain.session_question && domain.valence === "risk" && (
        <p className="psy-question">
          <span className="meta">Para preguntar en sesión</span>
          <br />
          {domain.session_question}
        </p>
      )}

      {canAdjudicate && (
        <div className="psy-actions">
          <button
            type="button"
            disabled={busy || domain.status === "confirmed"}
            onClick={() => onAdjudicate(domain.observation_id, "confirmed")}
          >
            Confirmar
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={busy || domain.status === "refuted"}
            onClick={() => onAdjudicate(domain.observation_id, "refuted")}
          >
            Refutar
          </button>
          {domain.status !== "inferred" && (
            <button
              type="button"
              className="linkish"
              disabled={busy}
              onClick={() => onAdjudicate(domain.observation_id, "inferred")}
            >
              Deshacer
            </button>
          )}
        </div>
      )}
    </article>
  );
}

export default function PsychosocialPanel({
  patientId,
  explanation,
  canAdjudicate,
  onChanged,
}: {
  patientId: string;
  explanation: PsychosocialExplanationOut;
  canAdjudicate: boolean;
  onChanged: () => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function adjudicate(observationId: string, status: "confirmed" | "refuted" | "inferred") {
    setBusy(true);
    setError(null);
    try {
      await api.post(
        `/api/v1/professional/patients/${patientId}/psychosocial/observations/${observationId}`,
        { status }
      );
      // Adjudication feeds the deterministic index, which can change the
      // level, so the whole record is reloaded rather than patched locally.
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const risk = explanation.domains.filter((d) => d.valence === "risk");
  const protective = explanation.domains.filter((d) => d.valence === "protective");
  const neutral = explanation.domains.filter((d) => d.valence === "neutral");

  return (
    <div className="psychosocial">
      <section className="explain-card explain-structural">
        <h3>Índice de vulnerabilidad psicosocial</h3>
        <IndexGauge index={explanation.index} band={explanation.band} />
        <p className="explain-headline">{explanation.summary}</p>
        <p className="explain-scale">{explanation.scale_note}</p>
        {explanation.driver_summary && <p className="explain-direction">{explanation.driver_summary}</p>}
        {explanation.protective_summary && <p className="meta">{explanation.protective_summary}</p>}
        <p className="meta">
          {explanation.active_count} dominio(s) activo(s) de {explanation.observation_count} observación(es)
          registradas · {explanation.confirmed_count} confirmada(s) · {explanation.refuted_count} refutada(s).
        </p>
        {explanation.caveats.length > 0 && (
          <ul className="explain-caveats">
            {explanation.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        )}
      </section>

      {explanation.leave_taking && (
        <section className="psy-leavetaking" role="note">
          <h3>⚠ Señal de despedida</h3>
          <p className="explain-headline">
            {explanation.leave_taking.category_label ?? explanation.leave_taking.category}
            {explanation.leave_taking.observed_at && (
              <span className="meta"> · {formatDateTime(explanation.leave_taking.observed_at)}</span>
            )}
          </p>
          {explanation.leave_taking.summary && <p>{explanation.leave_taking.summary}</p>}
          {explanation.leave_taking.quote && (
            <blockquote className="evidence-quote">
              <span className="meta">Sus palabras</span>
              <br />
              {explanation.leave_taking.quote}
            </blockquote>
          )}
          {explanation.leave_taking_note && <p className="meta">{explanation.leave_taking_note}</p>}
        </section>
      )}

      <section className="explain-card">
        <h3>Índices deterministas</h3>
        <p className="meta">
          Cuatro números, no uno: aritmética ordinaria sobre las observaciones registradas, sin ningún
          modelo en el cálculo. Un índice «sin datos» significa exactamente eso, no que no haya problema.
        </p>
        <IndexReadings readings={explanation.index_readings} />
        {explanation.interpersonal_recent_evidence.length > 0 && (
          <p className="explain-direction">
            Riesgo interpersonal expresado en los últimos 14 días:{" "}
            {explanation.interpersonal_recent_evidence.join(", ")}.
          </p>
        )}
      </section>

      {explanation.session_questions.length > 0 && (
        <section className="psy-questions">
          <h3>Preguntas para la próxima sesión</h3>
          <p className="meta">
            Generadas a partir de lo que se está moviendo ahora mismo, no de una plantilla fija.
          </p>
          <ol className="plain-list">
            {explanation.session_questions.map((item) => (
              <li key={item.domain}>
                <strong>{item.domain_label}</strong> <span className="meta">· {item.reason}</span>
                <div>{item.question}</div>
                {item.quote && <blockquote className="evidence-quote">{item.quote}</blockquote>}
              </li>
            ))}
          </ol>
        </section>
      )}

      {explanation.has_acute_change && (
        <section className="psy-acute">
          <h3>Cambios recientes (últimos 14 días)</h3>
          <p className="meta">{explanation.acute_note}</p>
          <ul className="plain-list">
            {explanation.acute_changes.map((change) => (
              <li key={change.observation_id}>
                <strong>
                  {change.label} · {change.category_label}
                </strong>{" "}
                <span className="meta">{formatDateTime(change.observed_at)}</span>
                <div>{change.summary}</div>
                {change.quote && <blockquote className="evidence-quote">{change.quote}</blockquote>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {error && <p className="error">{error}</p>}
      {!canAdjudicate && (
        <p className="info">
          Solo el terapeuta con asignación activa puede confirmar o refutar observaciones (RBAC).
        </p>
      )}

      {explanation.domains.length === 0 && (
        <p className="meta">
          Todavía no hay contexto psicosocial. El Agente 4 solo extrae lo que el paciente cuenta
          espontáneamente en el chat o en el diario. Si nunca ha hablado de su vivienda, su apoyo o su
          situación económica, este panel estará vacío — y ese vacío es en sí mismo información: puede ser un
          buen tema para la próxima sesión.
        </p>
      )}

      {risk.length > 0 && (
        <>
          <h3>Factores adversos</h3>
          <div className="psy-grid">
            {risk.map((domain) => (
              <DomainCard
                key={domain.observation_id}
                domain={domain}
                onAdjudicate={adjudicate}
                busy={busy}
                canAdjudicate={canAdjudicate}
              />
            ))}
          </div>
        </>
      )}

      {protective.length > 0 && (
        <>
          <h3>Factores protectores</h3>
          <div className="psy-grid">
            {protective.map((domain) => (
              <DomainCard
                key={domain.observation_id}
                domain={domain}
                onAdjudicate={adjudicate}
                busy={busy}
                canAdjudicate={canAdjudicate}
              />
            ))}
          </div>
        </>
      )}

      {neutral.length > 0 && (
        <>
          <h3>Contexto descriptivo</h3>
          <div className="psy-grid">
            {neutral.map((domain) => (
              <DomainCard
                key={domain.observation_id}
                domain={domain}
                onAdjudicate={adjudicate}
                busy={busy}
                canAdjudicate={canAdjudicate}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
