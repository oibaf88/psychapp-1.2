import { useEffect, useState } from "react";
import BreathingPacer from "../components/BreathingPacer";
import { api } from "../api";

interface ResourcesResponse {
  safe_grounding_alternatives: string[];
}

const STOP_STEPS = [
  { letter: "S", text: "Stop (Para) — detente, no reacciones automáticamente." },
  { letter: "T", text: "Take a step back (Da un paso atrás) — aléjate mentalmente de la situación." },
  { letter: "O", text: "Observe (Observa) — qué sientes, piensas y qué está pasando alrededor." },
  { letter: "P", text: "Proceed mindfully (Procede con conciencia) — elige tu siguiente paso." },
];

export default function WavePage() {
  const [urgeLevel, setUrgeLevel] = useState(5);
  const [alternatives, setAlternatives] = useState<string[]>([]);

  useEffect(() => {
    api
      .get<ResourcesResponse>("/api/v1/safety-plan/resources")
      .then((r) => setAlternatives(r.safe_grounding_alternatives))
      .catch(() => undefined);
  }, []);

  const waveHeight = 20 + urgeLevel * 8; // purely visual, not clinical

  return (
    <div className="page">
      <h1>Metáfora de la Ola (Urge Surfing)</h1>
      <p className="subtitle">
        Un craving es como una ola: sube, alcanza un pico y baja sola, aunque no hagas nada para detenerla. No tienes
        que luchar contra ella, solo mantenerte a flote mientras pasa.
      </p>

      <section className="card">
        <label>
          ¿Qué tan intensa sientes la urgencia ahora? (0-10): {urgeLevel}
          <input type="range" min={0} max={10} value={urgeLevel} onChange={(e) => setUrgeLevel(Number(e.target.value))} />
        </label>
        <div className="wave-visual" aria-hidden="true">
          <div className="wave" style={{ height: `${waveHeight}px` }} />
        </div>
        <p>
          {urgeLevel <= 3 && "Está bajando. Sigue observando sin juzgarte."}
          {urgeLevel > 3 && urgeLevel <= 7 && "Está en su punto medio. Respira y date tiempo: no dura para siempre."}
          {urgeLevel > 7 && "Está en su punto más alto. Es el momento más difícil, pero también empezará a bajar."}
        </p>
      </section>

      <section className="card">
        <h2>Respiración guiada</h2>
        <BreathingPacer />
      </section>

      <section className="card">
        <h2>DBT · STOP</h2>
        <ul>
          {STOP_STEPS.map((s) => (
            <li key={s.letter}>
              <strong>{s.letter}</strong> — {s.text}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>Anclaje sensorial seguro</h2>
        <p className="info">
          No usamos técnicas de frío intenso ni dolor físico (hielo, gomas elásticas) porque pueden ser
          contraproducentes. En su lugar:
        </p>
        <ul>
          {alternatives.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
