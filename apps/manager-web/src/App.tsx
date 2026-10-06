import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./auth";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { RoleGate, PermGate, HomeRedirect } from "./components/RoleGate";
import { Layout } from "./components/Layout";
import LoginPage from "./routes/LoginPage";
import RegisterPage from "./routes/RegisterPage";
import VerifyEmailPage from "./routes/VerifyEmailPage";
import ResetPasswordPage from "./routes/ResetPasswordPage";
import DashboardPage from "./routes/DashboardPage";
import AdminPage from "./routes/AdminPage";
import ProfilePage from "./routes/ProfilePage";
import GroupsPage from "./routes/GroupsPage";
import ResourcesPage from "./routes/ResourcesPage";
import IntegrationClientsPage from "./routes/IntegrationClientsPage";
import OidcCallbackPage from "./routes/OidcCallbackPage";
import { PERM_REWARD_ACCESS } from "./auth/permissions";
import CredentialsPage from "./reward/pages/CredentialsPage";
import JobsPage from "./reward/pages/JobsPage";
import JobNewPage from "./reward/pages/JobNewPage";
import JobDetailPage from "./reward/pages/JobDetailPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/callback" element={<OidcCallbackPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            {/* Dashboard/quản trị — admin, dash_admin & viewer */}
            <Route element={<RoleGate allow={["admin", "dash_admin", "viewer"]} />}>
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/groups" element={<GroupsPage />} />
              <Route path="/resources" element={<ResourcesPage />} />
            </Route>
            <Route element={<RoleGate allow={["admin", "dash_admin"]} />}>
              <Route path="/manage" element={<AdminPage />} />
            </Route>
            <Route path="/profile" element={<ProfilePage />} />
            <Route path="/integrations" element={<IntegrationClientsPage />} />

            {/* Module phân phối điểm Discord — quyền do claim `perms` (reward:access); admin được coi là có mọi quyền */}
            <Route element={<PermGate perm={PERM_REWARD_ACCESS} />}>
              <Route path="/reward/credentials" element={<CredentialsPage />} />
              <Route path="/reward/jobs" element={<JobsPage />} />
              <Route path="/reward/jobs/new" element={<JobNewPage />} />
              <Route path="/reward/jobs/:id" element={<JobDetailPage />} />
            </Route>

            <Route path="/" element={<HomeRedirect />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
