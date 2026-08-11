import { useEffect, useState } from "react";

type BreathPhase = "inhale" | "hold" | "exhale";

const PHASES: Array<{ id: BreathPhase; label: string; durationMs: number }> = [
  { id: "inhale", label: "Inhala suave", durationMs: 4000 },
  { id: "hold", label: "Sosten un momento", durationMs: 2000 },
  { id: "exhale", label: "Exhala lento", durationMs: 6000 },
];

const TOTAL_SECONDS = PHASES.reduce((sum, phase) => sum + phase.durationMs, 0) / 1000;

export default function BreathingPacer() {
  const [running, setRunning] = useState(false);
  const [phaseIndex, setPhaseIndex] = useState(0);
  const phase = PHASES[phaseIndex];

  useEffect(() => {
    if (!running) return undefined;

    const timer = window.setTimeout(() => {
      setPhaseIndex((current) => (current + 1) % PHASES.length);
    }, phase.durationMs);

    return () => window.clearTimeout(timer);
  }, [running, phase.durationMs]);

  function toggle() {
    if (running) {
      setRunning(false);
      setPhaseIndex(0);
      return;
    }

    setPhaseIndex(0);
    setRunning(true);
  }

  return (
    <div className="breathing-pacer">
      <div className={`breathing-orb ${running ? phase.id : "idle"}`} aria-hidden="true">
        <span />
      </div>
      <p className="breathing-label">{running ? phase.label : "Pulsa iniciar para comenzar"}</p>
      <p className="breathing-ratio">4 s inspirar - 2 s sostener - 6 s exhalar - {TOTAL_SECONDS}s por ciclo</p>
      <button onClick={toggle}>{running ? "Detener" : "Iniciar respiracion guiada"}</button>
      <p className="disclaimer">
        Esto es una guia visual de ritmo respiratorio, no un dispositivo medico ni biofeedback real: no mide tu cuerpo.
      </p>
    </div>
  );
}
