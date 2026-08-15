import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";
import NavBar from "./components/NavBar";
import CrisisButton from "./components/CrisisButton";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import PatientDashboard from "./pages/PatientDashboard";
import DiaryPage from "./pages/DiaryPage";
import ChatPage from "./pages/ChatPage";
import WavePage from "./pages/WavePage";
import SafetyPlanPage from "./pages/SafetyPlanPage";
import ProfessionalDashboard from "./pages/ProfessionalDashboard";
import AlertsPage from "./pages/AlertsPage";
import PatientDetailPage from "./pages/PatientDetailPage";
import AssignmentsPage from "./pages/AssignmentsPage";
import ConsentsPage from "./pages/ConsentsPage";
import FactsPage from "./pages/FactsPage";
import NotificationsPage from "./pages/NotificationsPage";
import AuditPage from "./pages/AuditPage";
import SettingsPage from "./pages/SettingsPage";
import CopilotPage from "./pages/CopilotPage";
import ManualPage from "./pages/ManualPage";

function Shell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return (
    <>
      <NavBar />
      <main>{children}</main>
      {user?.role === "patient" && <CrisisButton />}
    </>
  );
}

function RoleHome() {
  const { user, loading } = useAuth();
  if (loading) return <div className="loading">Cargando...</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role === "patient") return <PatientDashboard />;
  return <Navigate to="/professional" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Shell>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/settings" element={<SettingsPage />} />

            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <RoleHome />
                </ProtectedRoute>
              }
            />

            {/* Patient routes */}
            <Route
              path="/diary"
              element={
                <ProtectedRoute patientOnly>
                  <DiaryPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/chat"
              element={
                <ProtectedRoute patientOnly>
                  <ChatPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/wave"
              element={
                <ProtectedRoute patientOnly>
                  <WavePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/safety-plan"
              element={
                <ProtectedRoute patientOnly>
                  <SafetyPlanPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/facts"
              element={
                <ProtectedRoute patientOnly>
                  <FactsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/consents"
              element={
                <ProtectedRoute patientOnly>
                  <ConsentsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/assignments"
              element={
                <ProtectedRoute patientOnly>
                  <AssignmentsPage />
                </ProtectedRoute>
              }
            />

            {/* Shared */}
            <Route
              path="/notifications"
              element={
                <ProtectedRoute>
                  <NotificationsPage />
                </ProtectedRoute>
              }
            />

            {/* Professional routes */}
            <Route
              path="/professional"
              element={
                <ProtectedRoute professionalOnly>
                  <ProfessionalDashboard />
                </ProtectedRoute>
              }
            />
            <Route
              path="/professional/alerts"
              element={
                <ProtectedRoute professionalOnly>
                  <AlertsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/professional/assignments"
              element={
                <ProtectedRoute professionalOnly>
                  <AssignmentsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/professional/audit"
              element={
                <ProtectedRoute roles={["supervisor", "admin_clinical"]}>
                  <AuditPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/professional/copilot"
              element={
                <ProtectedRoute roles={["therapist", "supervisor"]}>
                  <CopilotPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/professional/manual"
              element={
                <ProtectedRoute professionalOnly>
                  <ManualPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/professional/patients/:patientId"
              element={
                <ProtectedRoute roles={["therapist", "supervisor"]}>
                  <PatientDetailPage />
                </ProtectedRoute>
              }
            />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Shell>
      </BrowserRouter>
    </AuthProvider>
  );
}
