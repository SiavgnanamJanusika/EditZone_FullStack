import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-brand-dark text-brand-gold">
        Loading...
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  if (!user.is_email_verified) {
    return <Navigate to="/verify-email" state={{ email: user.email }} replace />;
  }
  if (!user.registration_complete) {
    return <Navigate to="/complete-profile" replace />;
  }
  return children;
}
