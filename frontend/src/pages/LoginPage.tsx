import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { homePathForRole } from "../api";
import { useAuth } from "../auth/AuthContext";

const SHOW_LOCAL_DEMO = import.meta.env.DEV;
const DEMO_ACCOUNTS = [
  { role: "Paciente", email: "patient@demo.psychapp.example.com" },
  { role: "Terapeuta", email: "therapist@demo.psychapp.example.com" },
  { role: "Supervisor", email: "supervisor@demo.psychapp.example.com" },
  { role: "Admin clinico", email: "admin@demo.psychapp.example.com" },
];

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState(SHOW_LOCAL_DEMO ? "patient@demo.psychapp.example.com" : "");
  const [password, setPassword] = useState(SHOW_LOCAL_DEMO ? "DemoPass123!" : "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user = await login(email, password);
      navigate(homePathForRole(user.role), { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <h1>PsychApp</h1>
      <p className="subtitle">
        Herramienta de autorregulacion y autoconciencia. No es un dispositivo medico ni sustituye a tu equipo de
        tratamiento.
      </p>
      {SHOW_LOCAL_DEMO && (
        <p className="info" style={{ textAlign: "left" }}>
          <strong>Movil (misma Wi-Fi):</strong> abre <code>http://192.168.1.213:5173</code> (IP de tu PC). Si no carga,
          en el PC ejecuta <code>start-for-phone.ps1</code> y acepta el UAC del firewall.{" "}
          <Link to="/settings">Ajustes / instalar</Link>
        </p>
      )}
      <form onSubmit={onSubmit} className="auth-form">
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Contrasena
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Entrando..." : "Entrar"}
        </button>
      </form>
      <p>
        No tienes cuenta? <Link to="/register">Registrate</Link>
      </p>
      {SHOW_LOCAL_DEMO && (
        <div className="demo-hint">
          <p>
            Cuentas demo (contrasena <code>DemoPass123!</code>) - clic para rellenar:
          </p>
          <ul>
            {DEMO_ACCOUNTS.map((d) => (
              <li key={d.email}>
                <button
                  type="button"
                  className="linkish"
                  onClick={() => {
                    setEmail(d.email);
                    setPassword("DemoPass123!");
                  }}
                >
                  {d.role}
                </button>
                : {d.email}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
