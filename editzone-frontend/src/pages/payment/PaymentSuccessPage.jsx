import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, Clock3, XCircle } from "lucide-react";
import UserNavbar from "../../components/navbar/UserNavbar";
import EditorNavbar from "../../components/navbar/EditorNavbar";
import { PrimaryButton, Logo } from "../../components/common/UI";
import { useAuth } from "../../context/AuthContext";
import api from "../../services/api";

export default function PaymentSuccessPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const orderId = searchParams.get("order_id");
  const [status, setStatus] = useState("PENDING");
  const [message, setMessage] = useState("Waiting for PayHere’s secure confirmation…");
  const [projectId, setProjectId] = useState("");
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    if (!orderId) {
      setStatus("failed");
      setMessage("No payment reference was provided.");
      return undefined;
    }
    let stopped = false;
    let attempts = 0;
    const check = async () => {
      try {
        const response = await api.get(`/payments/${encodeURIComponent(orderId)}/status`);
        if (stopped) return;
        setStatus(response.data.status);
        setProjectId(response.data.project_id || "");
        if (response.data.status === "SUCCESS" && response.data.callback_verified) {
          setMessage("Payment verified successfully. The project and editor earnings records are updated.");
          return;
        }
        if (response.data.status === "AUTHORIZED") {
          setMessage("Payment status: PROTECTED. It will be released only after the final video is verified and you approve it.");
          return;
        }
        if (["CANCELLED", "FAILED", "REFUNDED", "CHARGEBACK", "CHARGEDBACK"].includes(response.data.status)) {
          setMessage(`Payment ${response.data.status.toLowerCase()}. No capture is pending.`);
          return;
        }
      } catch (error) {
        if (!stopped) setMessage(error.response?.data?.message || "Unable to verify payment status.");
      }
      attempts += 1;
      if (!stopped && attempts < 30) window.setTimeout(check, 2000);
      else if (!stopped) setMessage("Confirmation is taking longer than expected. You can safely check again from Order History.");
    };
    check();
    return () => { stopped = true; };
  }, [orderId, retryKey]);

  const successful = ["SUCCESS", "AUTHORIZED", "CAPTURED"].includes(status);
  const terminalFailure = ["CANCELLED", "FAILED", "REFUNDED", "CHARGEBACK", "CHARGEDBACK"].includes(status);
  const StatusIcon = successful ? CheckCircle2 : terminalFailure ? XCircle : Clock3;
  const destination = "/order-history";

  return (
    <div className="min-h-screen bg-brand-dark">
      {user?.role === "editor" ? <EditorNavbar /> : <UserNavbar />}
      <section className="max-w-md mx-auto px-6 py-24 text-center">
        <div className="flex justify-center mb-6"><Logo size={60} /></div>
        <div className="glass motion-panel rounded-2xl p-10">
          <StatusIcon className={`${successful ? "text-green-400" : terminalFailure ? "text-red-400" : "text-amber-400"} mx-auto mb-4`} size={56} />
          <h1 className="font-display text-2xl font-bold mb-2">
            {successful ? "Authorization Verified" : terminalFailure ? "Payment Not Completed" : "Verifying Payment"}
          </h1>
          <p className="text-gray-400 text-sm mb-8">{message}</p>
          <PrimaryButton className="w-full" onClick={() => navigate(destination)}>
            Go to History
          </PrimaryButton>
          {!successful && !terminalFailure && <button type="button" onClick={() => setRetryKey((value) => value + 1)} className="mt-3 w-full rounded-xl border border-white/10 px-4 py-3 text-sm text-slate-200">Retry status check</button>}
          {projectId && <button type="button" onClick={() => navigate(`/chat/${projectId}`)} className="mt-3 w-full rounded-xl border border-white/10 px-4 py-3 text-sm text-slate-200">Back to chat</button>}
        </div>
      </section>
    </div>
  );
}
