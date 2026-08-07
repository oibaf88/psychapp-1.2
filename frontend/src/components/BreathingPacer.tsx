import { useState } from "react";

/**
 * Visual breathing pacer, ~5.5-6 breaths/min (doc 3 & doc 6: "ritmo
 * 5.5-6 resp/min para favorecer activación parasimpática").
 * IMPORTANT (doc 6): this is explicitly NOT biofeedback -- it does not
 * read any sensor -- and the UI must say so.
 */
const INHALE_MS = 4000;
const EXHALE_MS = 6000; // ~6s exhale -> 10s cycle -> 6 breaths/min

export default function BreathingPacer() {
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<"inhale" | "exhale">("inhale");

  function start() {
    setRunning(true);
    setPhase("inhale");
    const cycle = () => {
      setPhase("inhale");
      const t1 = setTimeout(() => setPhase("exhale"), INHALE_MS);
      const t2 = setTimeout(cycle, INHALE_MS + EXHALE_MS);
      return () => {
        clearTimeout(t1);
        clearTimeout(t2);
      };
    };
    cycle();
  }

  function stop() {
    setRunning(false);
  }

  return (
    <div className="breathing-pacer">
      <div className={`breathing-circle ${running ? phase : "idle"}`} />
      <p className="breathing-label">
        {running ? (phase === "inhale" ? "Inhala..." : "Exhala...") : "Pulsa iniciar para comenzar"}
      </p>
      <button onClick={running ? stop : start}>{running ? "Detener" : "Iniciar respiración guiada"}</button>
      <p className="disclaimer">
        Esto es una guía visual de ritmo respiratorio, no un dispositivo médico ni biofeedback real: no mide tu cuerpo.
      </p>
    </div>
  );
}
