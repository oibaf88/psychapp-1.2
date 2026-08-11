import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { homePathForRole } from "../api";
import { useAuth } from "../auth/AuthContext";

export default function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user = await register(email, password, displayName);
      navigate(homePathForRole(user.role), { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <h1>Crear cuenta</h1>
      <form onSubmit={onSubmit} className="auth-form">
        <label>
          Nombre
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} required />
        </label>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Contrasena (minimo 8 caracteres)
          <input type="password" minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        <p className="info">
          El registro publico crea cuentas de paciente. Las cuentas profesionales se provisionan de forma interna.
        </p>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Creando..." : "Crear cuenta"}
        </button>
      </form>
      <p>
        <Link to="/login">Volver a entrar</Link>
      </p>
    </div>
  );
}
