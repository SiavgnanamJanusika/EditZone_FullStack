import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function RoleBasedRoute({ roles, children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  if (!roles.includes(user.role)) {
    const fallback = user.role === "editor" ? "/editor/dashboard" : user.role === "admin" ? "/admin" : "/editors";
    return <Navigate to={fallback} replace />;
  }
  return children;
}
