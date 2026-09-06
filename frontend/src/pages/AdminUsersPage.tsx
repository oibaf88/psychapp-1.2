import { FormEvent, useEffect, useState } from "react";
import { api, ROLE_LABELS, type UserRole } from "../api";
import { useAuth } from "../auth/AuthContext";

type ProvisionableRole = Exclude<UserRole, "patient">;

interface AdminUserOut {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  locale: string;
  is_active: boolean;
  created_at: string;
}

const ALL_ROLES: UserRole[] = ["patient", "therapist", "supervisor", "admin_clinical"];
const PROVISIONABLE_ROLES: ProvisionableRole[] = ["therapist", "supervisor", "admin_clinical"];

export default function AdminUsersPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<AdminUserOut[]>([]);
  const [draftRoles, setDraftRoles] = useState<Record<string, UserRole>>({});
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [provisionRole, setProvisionRole] = useState<ProvisionableRole>("therapist");

  async function load() {
    setError(null);
    try {
      const data = await api.get<AdminUserOut[]>("/api/v1/admin/users");
      setUsers(data);
      setDraftRoles(Object.fromEntries(data.map((item) => [item.id, item.role])));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function provision(e: FormEvent) {
    e.preventDefault();
    setBusy("provision");
    setError(null);
    setNotice(null);
    try {
      const created = await api.post<AdminUserOut>("/api/v1/admin/users", {
        email,
        password,
        display_name: displayName,
        role: provisionRole,
      });
      setUsers((current) => [created, ...current]);
      setDraftRoles((current) => ({ ...current, [created.id]: created.role }));
      setDisplayName("");
      setEmail("");
      setPassword("");
      setProvisionRole("therapist");
      setNotice(`Cuenta profesional creada para ${created.email}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function saveRole(target: AdminUserOut) {
    const nextRole = draftRoles[target.id] ?? target.role;
    if (nextRole === target.role) return;
    if (target.id === currentUser?.id) {
      setError("No puedes cambiar tu propio rol administrativo desde esta pantalla.");
      return;
    }

    const confirmed = window.confirm(
      `Cambiar ${target.email} de ${ROLE_LABELS[target.role]} a ${ROLE_LABELS[nextRole]}?`,
    );
    if (!confirmed) return;

    setBusy(target.id);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.put<AdminUserOut>(`/api/v1/admin/users/${target.id}/role`, { role: nextRole });
      setUsers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setDraftRoles((current) => ({ ...current, [updated.id]: updated.role }));
      setNotice(`Rol actualizado: ${updated.email} → ${ROLE_LABELS[updated.role]}.`);
    } catch (e) {
      setDraftRoles((current) => ({ ...current, [target.id]: target.role }));
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const normalizedQuery = query.trim().toLowerCase();
  const visibleUsers = normalizedQuery
    ? users.filter(
        (item) =>
          item.email.toLowerCase().includes(normalizedQuery) ||
          item.display_name.toLowerCase().includes(normalizedQuery) ||
          ROLE_LABELS[item.role].toLowerCase().includes(normalizedQuery),
      )
    : users;

  return (
    <div className="page">
      <h1>Gestión de usuarios</h1>
      <p className="subtitle">
        El registro público continúa creando exclusivamente pacientes. Desde aquí un administrador clínico puede
        provisionar cuentas profesionales o cambiar el perfil de una cuenta existente.
      </p>

      {error && <p className="error">{error}</p>}
      {notice && <p className="info">{notice}</p>}

      <section className="card">
        <h2>Crear cuenta profesional</h2>
        <p className="meta">
          La contraseña se almacena únicamente como hash. Entrega las credenciales iniciales al profesional por un canal
          adecuado; la aplicación no muestra contraseñas guardadas.
        </p>
        <form className="auth-form" onSubmit={provision}>
          <label>
            Nombre
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} required maxLength={255} />
          </label>
          <label>
            Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label>
            Contraseña inicial
            <input
              type="password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <label>
            Perfil
            <select
              value={provisionRole}
              onChange={(e) => setProvisionRole(e.target.value as ProvisionableRole)}
            >
              {PROVISIONABLE_ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={busy === "provision"}>
            {busy === "provision" ? "Creando..." : "Crear cuenta profesional"}
          </button>
        </form>
      </section>

      <section className="card">
        <h2>Usuarios existentes</h2>
        <label>
          Buscar
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Nombre, email o perfil"
          />
        </label>
        <p className="meta">
          Los cambios de rol quedan registrados en auditoría. Promover a un paciente no borra sus datos; si una cuenta
          vuelve a paciente, se crea su plan de seguridad solo si faltaba.
        </p>
      </section>

      <div className="stack">
        {visibleUsers.map((item) => {
          const isSelf = item.id === currentUser?.id;
          const draftRole = draftRoles[item.id] ?? item.role;
          return (
            <article className="card" key={item.id}>
              <h2>{item.display_name}</h2>
              <p>
                <strong>{item.email}</strong>
              </p>
              <p className="meta">
                Perfil actual: <strong>{ROLE_LABELS[item.role]}</strong> · Cuenta {item.is_active ? "activa" : "inactiva"}
                {" · "}Creada: {new Date(item.created_at).toLocaleString()}
              </p>

              <div className="alert-actions">
                <label>
                  Nuevo perfil
                  <select
                    value={draftRole}
                    disabled={isSelf || busy === item.id}
                    onChange={(e) =>
                      setDraftRoles((current) => ({ ...current, [item.id]: e.target.value as UserRole }))
                    }
                  >
                    {ALL_ROLES.map((role) => (
                      <option key={role} value={role}>
                        {ROLE_LABELS[role]}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={isSelf || busy === item.id || draftRole === item.role}
                  onClick={() => void saveRole(item)}
                >
                  {busy === item.id ? "Guardando..." : "Guardar rol"}
                </button>
              </div>
              {isSelf && <p className="meta">Tu propio rol administrativo está bloqueado para evitar un auto-bloqueo.</p>}
            </article>
          );
        })}
        {visibleUsers.length === 0 && <p className="info">No hay usuarios que coincidan con la búsqueda.</p>}
      </div>
    </div>
  );
}
