import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { SessionWarning } from "./SessionWarning";
import { SessionExpired } from "./SessionExpired";
import { Skeleton } from "./ui/skeleton";

export function ProtectedRoute() {
  const { user, loading, sessionExpired, sessionReason, showWarning, warningSeconds, continueSession, logout, dismissExpired } = useAuth();

  if (loading) {
    return (
      <div role="status" className="flex min-h-dvh items-center justify-center p-4">
        <div className="flex flex-col items-center gap-3">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-32" />
          <p className="text-sm text-muted-foreground">Đang kiểm tra phiên đăng nhập</p>
        </div>
      </div>
    );
  }

  if (!user && !sessionExpired) {
    return <Navigate to="/login" replace />;
  }

  return (
    <>
      <Outlet />
      {showWarning && !sessionExpired && (
        <SessionWarning seconds={warningSeconds} onContinue={continueSession} onLogout={logout} />
      )}
      {sessionExpired && <SessionExpired onLogin={dismissExpired} reason={sessionReason} />}
    </>
  );
}
