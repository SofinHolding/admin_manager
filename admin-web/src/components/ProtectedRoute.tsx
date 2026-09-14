import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { SessionWarning } from "./SessionWarning";
import { SessionExpired } from "./SessionExpired";
import { Skeleton } from "./ui/skeleton";

export function ProtectedRoute() {
  const { user, loading, sessionExpired, showWarning, warningSeconds, continueSession, logout, dismissExpired } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-32" />
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
      {showWarning && (
        <SessionWarning seconds={warningSeconds} onContinue={continueSession} onLogout={logout} />
      )}
      {sessionExpired && <SessionExpired onLogin={dismissExpired} />}
    </>
  );
}
