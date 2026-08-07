import { Navigate } from "react-router-dom";
import { ReactNode } from "react";
import { homePathForRole, UserRole } from "../api";
import { useAuth } from "../auth/AuthContext";

export default function ProtectedRoute({
  children,
  professionalOnly = false,
  patientOnly = false,
  roles,
}: {
  children: ReactNode;
  professionalOnly?: boolean;
  patientOnly?: boolean;
  roles?: UserRole[];
}) {
  const { user, loading } = useAuth();

  if (loading) return <div className="loading">Cargando...</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (professionalOnly && user.role === "patient") return <Navigate to="/" replace />;
  if (patientOnly && user.role !== "patient") return <Navigate to="/professional" replace />;
  if (roles && !roles.includes(user.role as UserRole)) {
    return <Navigate to={homePathForRole(user.role)} replace />;
  }

  return <>{children}</>;
}
