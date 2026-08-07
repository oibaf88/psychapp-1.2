import { useEffect, useState } from "react";
import { api, ConsentOut, CONSENT_LABELS } from "../api";

const TYPES = ["data_processing", "professional_sharing", "crisis_sms", "research"] as const;

export default function ConsentsPage() {
  const [consents, setConsents] = useState<ConsentOut[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setConsents(await api.get<ConsentOut[]>("/api/v1/consents"));
  }

  useEffect(() => {
    load().catch((e) => setError((e as Error).message));
  }, []);

  function latestFor(type: string): ConsentOut | undefined {
    return consents.find((c) => c.consent_type === type && !c.revoked_at);
  }

  async function setConsent(type: string, granted: boolean) {
    setMessage(null);
    setError(null);
    try {
      await api.post("/api/v1/consents", { consent_type: type, granted });
      setMessage(granted ? "Consentimiento concedido." : "Consentimiento revocado.");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="page">
      <h1>Consentimientos</h1>
      <p className="subtitle">
        Consentimiento granular y revocable por propósito. Cada cambio crea una nueva versión (historial conservado).
      </p>
      {message && <p className="info">{message}</p>}
      {error && <p className="error">{error}</p>}

      {TYPES.map((type) => {
        const current = latestFor(type);
        const active = !!(current && current.granted && !current.revoked_at);
        return (
          <section key={type} className="card">
            <h2>{CONSENT_LABELS[type] || type}</h2>
            <p className="meta">
              Estado actual:{" "}
              <strong className={active ? "badge-ok" : "badge-off"}>{active ? "Concedido" : "No concedido / revocado"}</strong>
              {current && <> · desde {new Date(current.granted_at).toLocaleString()}</>}
            </p>
            <div className="alert-actions">
              {!active && <button onClick={() => setConsent(type, true)}>Conceder</button>}
              {active && (
                <button className="btn-secondary" onClick={() => setConsent(type, false)}>
                  Revocar
                </button>
              )}
            </div>
          </section>
        );
      })}

      <section className="card">
        <h2>Historial</h2>
        <ul className="plain-list">
          {consents.map((c) => (
            <li key={c.id}>
              <strong>{CONSENT_LABELS[c.consent_type] || c.consent_type}</strong>: {c.granted ? "concedido" : "denegado"}
              {c.revoked_at ? ` · revocado ${new Date(c.revoked_at).toLocaleString()}` : ""} ·{" "}
              {new Date(c.granted_at).toLocaleString()}
            </li>
          ))}
          {consents.length === 0 && <li>Sin registros.</li>}
        </ul>
      </section>
    </div>
  );
}
