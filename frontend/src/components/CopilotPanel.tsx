/**
 * Agent 3 — the therapist's conversation about one patient.
 *
 * The model is given the patient's check-ins, diary, chat with Agent 1,
 * confirmed facts, Agent 2 signals, assessments and alerts, and is required
 * to cite the source and date of every clinical statement. It is read-only:
 * nothing it says can create a fact, a signal, an assessment or an alert,
 * so it can never change the patient's alert level or what the patient sees.
 */
import { FormEvent, useEffect, useRef, useState } from "react";
import { CopilotMessageOut, api, formatDateTime } from "../api";

const SUGGESTIONS = [
  "Resúmeme la situación actual y qué ha cambiado esta semana.",
  "¿De qué ha hablado en el chat en los últimos días?",
  "¿Por qué saltó la última alerta? Cítame el texto.",
  "¿Hay contradicciones entre lo que escribe y lo que marca en los check-ins?",
  "¿Qué preguntaría en la próxima sesión y por qué?",
];

export default function CopilotPanel({
  patientId,
  patientName,
}: {
  patientId: string;
  patientName?: string;
}) {
  const [messages, setMessages] = useState<CopilotMessageOut[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState(60);
  const endRef = useRef<HTMLDivElement | null>(null);

  async function load() {
    setError(null);
    try {
      setMessages(
        await api.get<CopilotMessageOut[]>(
          `/api/v1/professional/patients/${patientId}/copilot/messages`
        )
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    setMessages([]);
    setDraft("");
    load().catch(() => undefined);
  }, [patientId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, busy]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    // Show the therapist's own turn immediately; the server persists it too.
    const optimistic: CopilotMessageOut = {
      id: `pending-${Date.now()}`,
      patient_id: patientId,
      role: "user",
      content: trimmed,
      kind: "question",
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimistic]);
    setDraft("");
    try {
      await api.post<CopilotMessageOut>(
        `/api/v1/professional/patients/${patientId}/copilot/messages`,
        { message: trimmed, window_days: windowDays }
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
      setMessages((current) => current.filter((m) => m.id !== optimistic.id));
      setDraft(trimmed);
    } finally {
      setBusy(false);
    }
  }

  async function summarize() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.post<CopilotMessageOut>(
        `/api/v1/professional/patients/${patientId}/copilot/summary`,
        { window_days: windowDays }
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    send(draft).catch(() => undefined);
  }

  return (
    <div className="copilot">
      <div className="copilot-toolbar">
        <button type="button" onClick={summarize} disabled={busy}>
          {busy ? "Generando…" : "Resumir situación del paciente"}
        </button>
        <label className="copilot-window">
          Ventana de expediente
          <select
            value={windowDays}
            onChange={(e) => setWindowDays(Number(e.target.value))}
            disabled={busy}
          >
            <option value={14}>14 días</option>
            <option value={30}>30 días</option>
            <option value={60}>60 días</option>
            <option value={90}>90 días</option>
            <option value={180}>180 días</option>
          </select>
        </label>
      </div>

      <p className="copilot-disclaimer">
        El copiloto lee el expediente de {patientName || "este paciente"} (check-ins, diario, chat con el
        asistente, hechos, señales del Agente 2, evaluaciones y alertas) y debe citar fecha y fuente en cada
        afirmación. <strong>Es solo lectura</strong>: no puede crear hechos, señales ni alertas, ni cambiar el
        nivel de riesgo. Verifica siempre en las pestañas de la ficha lo que te diga.
      </p>

      <div className="copilot-window-box">
        {messages.length === 0 && !busy && (
          <div className="copilot-empty">
            <p>Todavía no has hablado con el copiloto sobre este paciente.</p>
            <p className="meta">Empieza con «Resumir situación del paciente» o con una de estas preguntas:</p>
          </div>
        )}
        {messages.map((message) => (
          <div
            key={message.id}
            className={`copilot-bubble copilot-${message.role}${
              message.error_kind ? " copilot-error" : ""
            }`}
          >
            <div className="meta copilot-meta">
              {message.role === "user" ? "Tú" : "Copiloto clínico"}
              {message.kind === "summary" ? " · resumen" : ""} · {formatDateTime(message.created_at)}
            </div>
            <div className="copilot-content">{message.content}</div>
          </div>
        ))}
        {busy && <div className="copilot-bubble copilot-assistant copilot-pending">Leyendo el expediente…</div>}
        <div ref={endRef} />
      </div>

      <div className="copilot-suggestions">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            className="chip"
            disabled={busy}
            onClick={() => send(suggestion)}
          >
            {suggestion}
          </button>
        ))}
      </div>

      <form className="chat-input-row" onSubmit={onSubmit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Pregunta algo sobre este paciente…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Enviar
        </button>
      </form>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
