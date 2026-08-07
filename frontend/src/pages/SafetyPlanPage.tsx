import { useEffect, useState } from "react";
import CrisisButton from "../components/CrisisButton";
import { api, SafetyPlanOut } from "../api";

interface ResourcesResponse {
  resources: { name: string; description: string; contact: string }[];
}

const FIELDS: { key: keyof SafetyPlanOut; label: string; placeholder: string }[] = [
  { key: "warning_signs", label: "Señales de alerta", placeholder: "Pensamientos, sensaciones o situaciones que indican que algo empieza a ir mal" },
  { key: "coping_strategies", label: "Estrategias de afrontamiento", placeholder: "Cosas que puedo hacer yo solo/a: la Ola, respiración, salir a caminar..." },
  { key: "social_supports", label: "Apoyos sociales", placeholder: "Personas con las que puedo hablar" },
  { key: "professional_contacts", label: "Contactos profesionales", placeholder: "Mi terapeuta, mi CAD de referencia..." },
  { key: "safe_environment", label: "Hacer mi entorno más seguro", placeholder: "Qué puedo retirar o alejar de mi alcance" },
  { key: "reasons_to_live", label: "Razones para vivir", placeholder: "Lo que más me importa" },
];

export default function SafetyPlanPage() {
  const [plan, setPlan] = useState<SafetyPlanOut | null>(null);
  const [resources, setResources] = useState<ResourcesResponse["resources"]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<SafetyPlanOut>("/api/v1/safety-plan").then(setPlan).catch(() => undefined);
    api
      .get<ResourcesResponse>("/api/v1/safety-plan/resources")
      .then((r) => setResources(r.resources))
      .catch(() => undefined);
  }, []);

  async function save() {
    if (!plan) return;
    setSaving(true);
    setSaved(false);
    try {
      const { id, updated_at, ...payload } = plan;
      const updated = await api.put<SafetyPlanOut>("/api/v1/safety-plan", payload);
      setPlan(updated);
      setSaved(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page">
      <h1>Mi plan de seguridad</h1>
      <CrisisButton />

      {plan && (
        <section className="card">
          {FIELDS.map((f) => (
            <label key={f.key}>
              {f.label}
              <textarea
                rows={2}
                value={(plan[f.key] as string) || ""}
                placeholder={f.placeholder}
                onChange={(e) => setPlan({ ...plan, [f.key]: e.target.value })}
              />
            </label>
          ))}
          <button onClick={save} disabled={saving}>
            {saving ? "Guardando..." : "Guardar plan"}
          </button>
          {saved && <p className="info">Guardado.</p>}
        </section>
      )}

      <section className="card">
        <h2>Recursos de emergencia (Madrid / España)</h2>
        <ul>
          {resources.map((r) => (
            <li key={r.name}>
              <strong>{r.name}</strong>: {r.description} — {r.contact}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
