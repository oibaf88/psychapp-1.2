import { Link } from "react-router-dom";

/**
 * Always-visible crisis access point (doc 3: "botón de crisis siempre
 * visible que conecte en un toque con 024, 112 y el plan de seguridad
 * personal"). Deliberately simple, static, and independent of any LLM or
 * network call other than the phone links themselves.
 */
export default function CrisisButton() {
  return (
    <div className="crisis-bar">
      <a href="tel:024" className="crisis-link">
        📞 Línea 024
      </a>
      <a href="tel:112" className="crisis-link">
        🚑 112
      </a>
      <Link to="/safety-plan" className="crisis-link">
        📋 Mi plan de seguridad
      </Link>
    </div>
  );
}
