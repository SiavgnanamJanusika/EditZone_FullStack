import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Routes, Route, useLocation } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { useAuth } from "./context/AuthContext";
import { SocketProvider } from "./context/SocketContext";
import ProtectedRoute from "./routes/ProtectedRoute";
import RoleBasedRoute from "./routes/RoleBasedRoute";
import { ToastProvider } from "./components/common/UX";
import { CinematicBackdrop } from "./components/common/VisualEffects";

const LandingPage = lazy(() => import("./pages/landing/LandingPage"));
const AboutPage = lazy(() => import("./pages/landing/AboutPage"));
const WhyUsPage = lazy(() => import("./pages/landing/WhyUsPage"));
const CreditsPage = lazy(() => import("./pages/landing/CreditsPage"));

const ChooseRolePage = lazy(() => import("./pages/auth/ChooseRolePage"));
const LoginPage = lazy(() => import("./pages/auth/LoginPage"));
const RegisterPage = lazy(() => import("./pages/auth/RegisterPage"));
const CompleteProfilePage = lazy(() => import("./pages/auth/CompleteProfilePage"));
const ForgotPasswordPage = lazy(() => import("./pages/auth/ForgotPasswordPage"));
const ResetPasswordPage = lazy(() => import("./pages/auth/ResetPasswordPage"));
const VerifyEmailPage = lazy(() => import("./pages/auth/VerifyEmailPage"));

const EditorsPage = lazy(() => import("./pages/user/EditorsPage"));
const EditorProfilePage = lazy(() => import("./pages/user/EditorProfilePage"));
const EditorStatusViewPage = lazy(() => import("./pages/user/EditorStatusViewPage"));
const OrderHistoryPage = lazy(() => import("./pages/user/OrderHistoryPage"));
const UserProfilePage = lazy(() => import("./pages/user/UserProfilePage"));

const EditorDashboard = lazy(() => import("./pages/editor/EditorDashboard"));
const EditorProfileEdit = lazy(() => import("./pages/editor/EditorProfileEdit"));
const EditorEarningsPage = lazy(() => import("./pages/editor/EditorEarningsPage"));
const EditorStatusPage = lazy(() => import("./pages/editor/EditorStatusPage"));
const EditorUpdatesPage = lazy(() => import("./pages/editor/EditorUpdatesPage"));
const EditorLayout = lazy(() => import("./components/editor/EditorLayout"));

const ChatPage = lazy(() => import("./pages/shared/ChatPage"));

const PaymentPage = lazy(() => import("./pages/payment/PaymentPage"));
const PaymentSuccessPage = lazy(() => import("./pages/payment/PaymentSuccessPage"));
const PaymentFailedPage = lazy(() => import("./pages/payment/PaymentFailedPage"));
const ApproveWorkPage = lazy(() => import("./pages/payment/ApproveWorkPage"));

const AdminDashboard = lazy(() => import("./pages/admin/AdminDashboard"));
const UserManagement = lazy(() => import("./pages/admin/UserManagement"));
const EditorManagement = lazy(() => import("./pages/admin/EditorManagement"));
const PaymentManagement = lazy(() => import("./pages/admin/PaymentManagement"));
const ProjectMonitoring = lazy(() => import("./pages/admin/ProjectMonitoring"));
const AdminManagementPage = lazy(() => import("./pages/admin/AdminManagementPage"));
const ReportsAnalyticsPage = lazy(() => import("./pages/admin/ReportsAnalyticsPage"));
const NotFoundPage = lazy(() => import("./pages/NotFoundPage"));

const routePreloads = [
  () => import("./pages/landing/AboutPage"),
  () => import("./pages/landing/WhyUsPage"),
  () => import("./pages/auth/LoginPage"),
  () => import("./pages/auth/RegisterPage"),
  () => import("./pages/user/EditorsPage"),
  () => import("./pages/user/OrderHistoryPage"),
  () => import("./pages/editor/EditorDashboard"),
  () => import("./pages/shared/ChatPage"),
];

function IdleRoutePrefetch() {
  useEffect(() => {
    const preload = () => routePreloads.forEach((load) => load().catch(() => undefined));
    if ("requestIdleCallback" in window) {
      const idleId = window.requestIdleCallback(preload, { timeout: 1800 });
      return () => { window.cancelIdleCallback(idleId); };
    }
    const timerId = window.setTimeout(preload, 600);
    return () => { window.clearTimeout(timerId); };
  }, []);
  return null;
}

function RouteLoader() {
  return (
    <div role="status" aria-live="polite" className="min-h-screen bg-brand-dark px-6 py-24">
      <span className="sr-only">Loading page</span>
      <div className="mx-auto max-w-4xl space-y-5">
        <div className="skeleton h-9 w-56 rounded-lg" />
        <div className="skeleton h-5 w-3/4 rounded-lg" />
        <div className="grid gap-4 sm:grid-cols-2"><div className="skeleton h-48 rounded-2xl" /><div className="skeleton h-48 rounded-2xl" /></div>
      </div>
    </div>
  );
}

function RequireLoggedIn({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center bg-brand-dark text-brand-gold">Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (!user.is_email_verified) return <Navigate to="/verify-email" state={{ email: user.email }} replace />;
  return children;
}

function GuestOnly({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <RouteLoader />;
  if (!user) return children;
  if (!user.is_email_verified) return <Navigate to="/verify-email" replace />;
  if (!user.registration_complete) return <Navigate to="/complete-profile" replace />;
  const destination = user.role === "editor" ? "/editor/dashboard" : user.role === "admin" ? "/admin" : "/editors";
  return <Navigate to={destination} replace />;
}

function AnimatedRoutes() {
  const location = useLocation();

  return (
    <div key={location.pathname} className="page-route-enter">
      <Routes location={location}>
        {/* Public */}
        <Route path="/" element={<LandingPage />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="/why-us" element={<WhyUsPage />} />
        <Route path="/credits" element={<CreditsPage />} />
        <Route path="/choose-role" element={<GuestOnly><ChooseRolePage /></GuestOnly>} />
        <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
        <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
        <Route path="/forgot-password" element={<GuestOnly><ForgotPasswordPage /></GuestOnly>} />
        <Route path="/reset-password" element={<GuestOnly><ResetPasswordPage /></GuestOnly>} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />

        {/* Post-login, pre-registration-complete */}
        <Route path="/complete-profile" element={<RequireLoggedIn><CompleteProfilePage /></RequireLoggedIn>} />

        {/* User */}
        <Route path="/editors" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><EditorsPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/editors/:editorId" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><EditorProfilePage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/editors/:editorId/status/:statusId" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><EditorStatusViewPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/order-history" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><OrderHistoryPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/profile" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><UserProfilePage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/subscription" element={<Navigate to="/order-history" replace />} />
        <Route path="/chat/:requestId" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><ChatPage role="user" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/chat/:requestId/project" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><ChatPage role="user" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/chat/:requestId/payment" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><ChatPage role="user" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/chat/:requestId/upload" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><ChatPage role="user" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/payment/:requestId" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><PaymentPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/payment/checkout/:quoteId" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><PaymentPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/payment-success" element={<ProtectedRoute><RoleBasedRoute roles={["user", "editor"]}><PaymentSuccessPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/payment-failed" element={<ProtectedRoute><RoleBasedRoute roles={["user", "editor"]}><PaymentFailedPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/payment/success" element={<ProtectedRoute><RoleBasedRoute roles={["user", "editor"]}><PaymentSuccessPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/payment/cancel" element={<ProtectedRoute><RoleBasedRoute roles={["user", "editor"]}><PaymentFailedPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/payment/pending" element={<ProtectedRoute><RoleBasedRoute roles={["user", "editor"]}><PaymentSuccessPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/approve-work/:requestId" element={<ProtectedRoute><RoleBasedRoute roles={["user"]}><ApproveWorkPage /></RoleBasedRoute></ProtectedRoute>} />

        {/* Editor */}
        <Route path="/editor" element={<ProtectedRoute><RoleBasedRoute roles={["editor"]}><EditorLayout /></RoleBasedRoute></ProtectedRoute>}>
          <Route index element={<Navigate to="dashboard" replace />} />
          <Route path="status" element={<EditorStatusPage />} />
          <Route path="dashboard" element={<EditorDashboard />} />
          <Route path="updates" element={<EditorUpdatesPage />} />
          <Route path="profile" element={<EditorProfileEdit />} />
          <Route path="earnings" element={<EditorEarningsPage />} />
        </Route>
        <Route path="/editor/chat/:requestId" element={<ProtectedRoute><RoleBasedRoute roles={["editor"]}><ChatPage role="editor" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/editor/chat/:requestId/project" element={<ProtectedRoute><RoleBasedRoute roles={["editor"]}><ChatPage role="editor" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/editor/chat/:requestId/payment" element={<ProtectedRoute><RoleBasedRoute roles={["editor"]}><ChatPage role="editor" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/editor/chat/:requestId/upload" element={<ProtectedRoute><RoleBasedRoute roles={["editor"]}><ChatPage role="editor" /></RoleBasedRoute></ProtectedRoute>} />

        {/* Admin */}
        <Route path="/admin" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><AdminDashboard /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/users" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><UserManagement /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/editors" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><EditorManagement /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/payments" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><PaymentManagement /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/projects" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><ProjectMonitoring /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/requests" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><AdminManagementPage type="requests" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/payment-protection" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><AdminManagementPage type="paymentProtection" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/disputes" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><AdminManagementPage type="disputes" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/chat-reports" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><AdminManagementPage type="chat-reports" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/reviews" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><AdminManagementPage type="reviews" /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/analytics" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><ReportsAnalyticsPage /></RoleBasedRoute></ProtectedRoute>} />
        <Route path="/admin/content" element={<ProtectedRoute><RoleBasedRoute roles={["admin"]}><AdminManagementPage type="content" /></RoleBasedRoute></ProtectedRoute>} />

        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <CinematicBackdrop />
      <ToastProvider>
        <AuthProvider>
          <IdleRoutePrefetch />
          <SocketProvider>
            <Suspense fallback={<RouteLoader />}>
              <AnimatedRoutes />
            </Suspense>
          </SocketProvider>
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
