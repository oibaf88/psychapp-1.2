import { CSSProperties, useEffect, useMemo, useState } from "react";
import BreathingPacer from "../components/BreathingPacer";
import { api } from "../api";

interface ResourcesResponse {
  safe_grounding_alternatives: string[];
}

const STOP_STEPS = [
  { letter: "S", text: "Stop: detente, no reacciones automaticamente." },
  { letter: "T", text: "Toma distancia: alejate mentalmente de la situacion." },
  { letter: "O", text: "Observa: que sientes, que piensas y que esta pasando alrededor." },
  { letter: "P", text: "Procede con conciencia: elige tu siguiente paso." },
];

function wavePath(level: number, offset: number) {
  const baseline = 118 - level * 5.2;
  const amplitude = 9 + level * 2.4;
  const width = 96;
  let path = `M ${-width + offset} ${baseline}`;

  for (let x = -width + offset; x < 384; x += width) {
    path += ` C ${x + width * 0.25} ${baseline - amplitude}, ${x + width * 0.75} ${
      baseline + amplitude
    }, ${x + width} ${baseline}`;
  }

  return `${path} L 384 160 L 0 160 Z`;
}

export default function WavePage() {
  const [urgeLevel, setUrgeLevel] = useState(5);
  const [alternatives, setAlternatives] = useState<string[]>([]);

  useEffect(() => {
    api
      .get<ResourcesResponse>("/api/v1/safety-plan/resources")
      .then((r) => setAlternatives(r.safe_grounding_alternatives))
      .catch(() => undefined);
  }, []);

  const waveFront = useMemo(() => wavePath(urgeLevel, 0), [urgeLevel]);
  const waveBack = useMemo(() => wavePath(Math.max(1, urgeLevel - 2), 36), [urgeLevel]);
  const waveStyle = {
    "--wave-duration": `${Math.max(5, 12 - urgeLevel * 0.6)}s`,
    "--wave-back-duration": `${Math.max(8, 16 - urgeLevel * 0.5)}s`,
  } as CSSProperties;

  return (
    <div className="page">
      <h1>Metafora de la Ola</h1>
      <p className="subtitle">
        La urgencia puede sentirse enorme cuando sube. Esta practica entrena observarla mientras se mueve, alcanza un
        pico y vuelve a bajar.
      </p>

      <section className="card">
        <label>
          Intensidad de la urgencia ahora (0-10): {urgeLevel}
          <input type="range" min={0} max={10} value={urgeLevel} onChange={(e) => setUrgeLevel(Number(e.target.value))} />
        </label>
        <div className="wave-visual" style={waveStyle} aria-hidden="true">
          <svg className="wave-svg" viewBox="0 0 320 160" preserveAspectRatio="none">
            <defs>
              <linearGradient id="waveFrontGradient" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="#73d2de" />
                <stop offset="46%" stopColor="#2f8fbd" />
                <stop offset="100%" stopColor="#1b4f8a" />
              </linearGradient>
              <linearGradient id="waveBackGradient" x1="0" x2="1" y1="0" y2="1">
                <stop offset="0%" stopColor="#b6e6ff" stopOpacity="0.7" />
                <stop offset="100%" stopColor="#5aa4d6" stopOpacity="0.55" />
              </linearGradient>
            </defs>
            <path className="wave-back" d={waveBack} />
            <path className="wave-front" d={waveFront} />
            <path className="wave-foam" d={wavePath(Math.min(10, urgeLevel + 1), 12)} />
          </svg>
        </div>
        <p>
          {urgeLevel <= 3 && "La ola esta perdiendo fuerza. Sigue observando sin perseguirla ni pelear con ella."}
          {urgeLevel > 3 && urgeLevel <= 7 && "Estas dentro de la ola. Respira, nota el movimiento y date tiempo."}
          {urgeLevel > 7 && "Este es el pico. Se siente muy intenso, pero el pico tambien se mueve y termina bajando."}
        </p>
      </section>

      <section className="card">
        <h2>Respiracion guiada</h2>
        <BreathingPacer />
      </section>

      <section className="card">
        <h2>DBT - STOP</h2>
        <ul>
          {STOP_STEPS.map((s) => (
            <li key={s.letter}>
              <strong>{s.letter}</strong> - {s.text}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>Anclaje sensorial seguro</h2>
        <p className="info">
          Evita tecnicas de frio intenso o dolor fisico. Prueba una alternativa sensorial suave y reversible:
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
