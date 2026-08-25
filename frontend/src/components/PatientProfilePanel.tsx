import { useCallback, useEffect, useState } from "react";

import { api } from "../api";

/**
 * What the system has accumulated about a patient, and what it plans to
 * bring up next.
 *
 * The point of showing it is that a clinician can disagree with it. A
 * portrait is a model's summary of a person in treatment; one nobody can
 * correct is one nobody should trust. The open threads are an agenda the
 * conversational agent actually follows, so editing them changes what the
 * patient gets asked next — which is exactly why it belongs to the
 * therapist and not to the model alone.
 */

export interface OpenThread {
  topic: string;
  note?: string | null;
  opened_at?: string | null;
  source?: string | null;
}

export interface PatientProfile {
  portrait: string | null;
  previous_portrait: string | null;
  portrait_version: number;
  portrait_updated_at: string | null;
  portrait_edited_by_clinician: boolean;
  open_threads: OpenThread[];
  linguistic_baseline: Record<string, { mean: number; std: number; n: number }> | null;
  linguistic_baseline_n: number;
  baseline_is_usable: boolean;
  minimum_signals_for_baseline: number;
}

const AXIS_LABELS: Record<string, string> = {
  rumination_score: "Rumiación",
  negative_valence: "Valencia negativa",
  urgency_level: "Urgencia",
  ambivalence: "Ambivalencia",
};

export default function PatientProfilePanel({
  patientId,
  canEdit,
}: {
  patientId: string;
  canEdit: boolean;
}) {
  const [profile, setProfile] = useState<PatientProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [portraitDraft, setPortraitDraft] = useState("");
  const [threadDraft, setThreadDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [showPrevious, setShowPrevious] = useState(false);

  const load = useCallback(() => {
    api
      .get<PatientProfile>(`/api/v1/professional/patients/${patientId}/profile`)
      .then((data) => {
        setProfile(data);
        setPortraitDraft(data.portrait || "");
        setError(null);
      })
      .catch((e) => setError((e as Error).message));
  }, [patientId]);

  useEffect(load, [load]);

  async function save(body: Record<string, unknown>, message: string) {
    setBusy(true);
    setSaved(null);
    try {
      const next = await api.put<PatientProfile>(
        `/api/v1/professional/patients/${patientId}/profile`,
        body,
      );
      setProfile(next);
      setPortraitDraft(next.portrait || "");
      setSaved(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function addThread() {
    const topic = threadDraft.trim();
    if (!topic || !profile) return;
    const threads = [...profile.open_threads, { topic }].map((t) => ({
      topic: t.topic,
      note: t.note || null,
    }));
    setThreadDraft("");
    void save({ open_threads: threads }, "Tema añadido.");
  }

  function removeThread(topic: string) {
    if (!profile) return;
    const threads = profile.open_threads
      .filter((t) => t.topic !== topic)
      .map((t) => ({ topic: t.topic, note: t.note || null }));
    void save({ open_threads: threads }, "Tema cerrado.");
  }

  if (error) return <p className="error">No se pudo cargar el perfil: {error}</p>;
  if (!profile) return <p className="meta">Cargando…</p>;

  const hasPortrait = Boolean(profile.portrait);

  return (
    <div className="profile-panel">
      <h3>Quién es esta persona</h3>
      {profile.portrait_edited_by_clinician ? (
        <p className="meta">
          Corregido por un profesional. El analizador puede añadir, pero se le indica que no
          contradiga lo que has escrito.
        </p>
      ) : (
        <p className="meta">
          Lo ha acumulado el sistema a partir de lo que el paciente ha ido contando. Es un resumen
          de un modelo: si algo no encaja, corrígelo.
        </p>
      )}

      {canEdit ? (
        <>
          <textarea
            rows={7}
            value={portraitDraft}
            placeholder="Todavía no hay retrato. Puedes escribir uno."
            onChange={(e) => setPortraitDraft(e.target.value)}
          />
          <div className="alert-actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || portraitDraft === (profile.portrait || "")}
              onClick={() => void save({ portrait: portraitDraft }, "Retrato guardado.")}
            >
              {busy ? "Guardando…" : "Guardar retrato"}
            </button>
            {profile.previous_portrait && (
              <button type="button" className="btn-secondary" onClick={() => setShowPrevious((v) => !v)}>
                {showPrevious ? "Ocultar versión anterior" : "Ver versión anterior"}
              </button>
            )}
          </div>
        </>
      ) : (
        <p>{profile.portrait || "Todavía no hay retrato."}</p>
      )}

      {showPrevious && profile.previous_portrait && (
        <blockquote className="meta">
          <strong>Versión {Math.max(profile.portrait_version - 1, 0)}:</strong>{" "}
          {profile.previous_portrait}
        </blockquote>
      )}

      <h3>Temas abiertos</h3>
      <p className="meta">
        Es la agenda que el asistente sigue con el paciente, no un cuestionario. Lo que pongas aquí
        es lo que intentará retomar; lo que quites, deja de sacarlo.
      </p>
      {profile.open_threads.length === 0 ? (
        <p className="meta">Ninguno ahora mismo.</p>
      ) : (
        <ul className="thread-list">
          {profile.open_threads.map((t) => (
            <li key={t.topic}>
              <strong>{t.topic}</strong>
              {t.note && <span className="meta"> — {t.note}</span>}
              {t.source === "clinician" && <span className="meta"> · añadido por un profesional</span>}
              {canEdit && (
                <button type="button" className="btn-secondary" onClick={() => removeThread(t.topic)}>
                  Cerrar
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canEdit && (
        <div className="alert-actions">
          <input
            type="text"
            value={threadDraft}
            placeholder="Qué te gustaría que explorase"
            onChange={(e) => setThreadDraft(e.target.value)}
          />
          <button type="button" className="btn-secondary" disabled={busy || !threadDraft.trim()} onClick={addThread}>
            Añadir
          </button>
        </div>
      )}

      <h3>Cómo puntúa habitualmente</h3>
      {profile.baseline_is_usable && profile.linguistic_baseline ? (
        <>
          <p className="meta">
            Sobre {profile.linguistic_baseline_n} textos suyos. El motor compara cada texto nuevo
            con estos valores además de con los umbrales fijos, nunca en lugar de ellos.
          </p>
          <dl className="llm-active-grid">
            {Object.entries(profile.linguistic_baseline).map(([axis, stats]) => (
              <div key={axis}>
                <dt>{AXIS_LABELS[axis] || axis}</dt>
                <dd>
                  {stats.mean.toFixed(2)} <span className="meta">± {stats.std.toFixed(2)}</span>
                </dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <p className="meta">
          Todavía no hay suficiente historial ({profile.linguistic_baseline_n} de{" "}
          {profile.minimum_signals_for_baseline} textos). Hasta entonces se evalúa solo con los
          umbrales fijos, igual que antes.
        </p>
      )}
      {saved && <p className="info">{saved}</p>}
      {!hasPortrait && !canEdit && <p className="meta">Solo el terapeuta asignado puede editarlo.</p>}
    </div>
  );
}
