import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./auth";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { RoleGate, HomeRedirect } from "./components/RoleGate";
import { Layout } from "./components/Layout";
import LoginPage from "./routes/LoginPage";
import RegisterPage from "./routes/RegisterPage";
import VerifyEmailPage from "./routes/VerifyEmailPage";
import ResetPasswordPage from "./routes/ResetPasswordPage";
import DashboardPage from "./routes/DashboardPage";
import AdminPage from "./routes/AdminPage";
import ProfilePage from "./routes/ProfilePage";
import CredentialsPage from "./reward/pages/CredentialsPage";
import JobsPage from "./reward/pages/JobsPage";
import JobNewPage from "./reward/pages/JobNewPage";
import JobDetailPage from "./reward/pages/JobDetailPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            {/* Dashboard/quản trị — admin & viewer */}
            <Route element={<RoleGate allow={["admin", "viewer"]} />}>
              <Route path="/dashboard" element={<DashboardPage />} />
            </Route>
            <Route element={<RoleGate allow={["admin"]} />}>
              <Route path="/manage" element={<AdminPage />} />
            </Route>
            <Route path="/profile" element={<ProfilePage />} />

            {/* Module phân phối điểm Discord — role discord & admin dùng chung template */}
            <Route element={<RoleGate allow={["discord", "admin"]} />}>
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
