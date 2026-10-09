import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./auth";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { RoleGate, PermGate, HomeRedirect } from "./components/RoleGate";
import { CapGate } from "./components/CapGate";
import { Layout } from "./components/Layout";
import LoginPage from "./routes/LoginPage";
import RegisterPage from "./routes/RegisterPage";
import VerifyEmailPage from "./routes/VerifyEmailPage";
import ResetPasswordPage from "./routes/ResetPasswordPage";
import DashboardPage from "./routes/DashboardPage";
import AdminPage from "./routes/AdminPage";
import ProfilePage from "./routes/ProfilePage";
import GroupsPage from "./routes/GroupsPage";
import OidcCallbackPage from "./routes/OidcCallbackPage";
import IntegrationClientsPage from "./routes/IntegrationClientsPage";
import ChannelsPage from "./routes/ChannelsPage";
import MyToolsPage from "./routes/MyToolsPage";
import { PERM_REWARD_ACCESS, CAP_MANUAL_CHANNEL_MGMT, CAP_TOOL_INTEGRATION } from "./auth/permissions";
import CredentialsPage from "./reward/pages/CredentialsPage";
import JobsPage from "./reward/pages/JobsPage";
import JobNewPage from "./reward/pages/JobNewPage";
import JobDetailPage from "./reward/pages/JobDetailPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        {/* Public */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/callback" element={<OidcCallbackPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />

        {/* Protected */}
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            {/* Profile — mọi user đã đăng nhập */}
            <Route path="/profile" element={<ProfilePage />} />

            {/* Admin zone — viewer / admin / dash_admin */}
            <Route element={<RoleGate allow={["admin", "dash_admin", "viewer"]} />}>
              <Route path="/admin/dashboard" element={<DashboardPage />} />
              <Route path="/admin/groups" element={<GroupsPage />} />
              {/* Reward dưới admin zone (admin cũng có reward:access) */}
              <Route element={<PermGate perm={PERM_REWARD_ACCESS} />}>
                <Route path="/admin/reward/credentials" element={<CredentialsPage />} />
                <Route path="/admin/reward/jobs" element={<JobsPage />} />
                <Route path="/admin/reward/jobs/new" element={<JobNewPage />} />
                <Route path="/admin/reward/jobs/:id" element={<JobDetailPage />} />
              </Route>
            </Route>
            <Route element={<RoleGate allow={["admin", "dash_admin"]} />}>
              <Route path="/admin/manage" element={<AdminPage />} />
            </Route>

            {/* Công cụ — capability:tool_integration (không cần role admin) */}
            <Route element={<CapGate cap={CAP_TOOL_INTEGRATION} />}>
              <Route path="/my-tools" element={<MyToolsPage />} />
              <Route path="/integrations" element={<IntegrationClientsPage />} />
            </Route>
            <Route element={<CapGate cap={CAP_MANUAL_CHANNEL_MGMT} />}>
              <Route path="/channels" element={<ChannelsPage />} />
            </Route>

            {/* Reward zone — user chỉ có reward:access, không có dashboard role */}
            <Route element={<PermGate perm={PERM_REWARD_ACCESS} />}>
              <Route path="/reward/credentials" element={<CredentialsPage />} />
              <Route path="/reward/jobs" element={<JobsPage />} />
              <Route path="/reward/jobs/new" element={<JobNewPage />} />
              <Route path="/reward/jobs/:id" element={<JobDetailPage />} />
            </Route>

            {/* Redirect từ root */}
            <Route path="/" element={<HomeRedirect />} />
            <Route path="/admin" element={<HomeRedirect />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
