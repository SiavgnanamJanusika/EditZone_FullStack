import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Clock3, XCircle } from "lucide-react";
import UserNavbar from "../../components/navbar/UserNavbar";
import EditorNavbar from "../../components/navbar/EditorNavbar";
import { Logo, PrimaryButton, OutlineButton } from "../../components/common/UI";
import { useAuth } from "../../context/AuthContext";
import api from "../../services/api";

export default function PaymentFailedPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [params] = useSearchParams();
  const orderId = params.get("order_id");
  const [status, setStatus] = useState("PENDING");
  const [message, setMessage] = useState("Checking the signed PayHere callback status…");
  useEffect(() => {
    if (!orderId) { setStatus("CANCELLED"); setMessage("No payment reference was provided."); return; }
    let stopped = false;
    api.get(`/payments/${encodeURIComponent(orderId)}/status`)
      .then(({ data }) => {
        if (stopped) return;
        setStatus(data.status);
        setMessage(["AUTHORIZED", "CAPTURED"].includes(data.status)
          ? "PayHere confirmed this payment. You can continue safely."
          : data.status === "PENDING"
            ? "The browser returned before PayHere’s callback. Check Order History shortly."
            : `PayHere reported ${data.status.toLowerCase()}.`);
      })
      .catch((error) => !stopped && setMessage(error.response?.data?.message || "Unable to confirm payment status."));
    return () => { stopped = true; };
  }, [orderId]);
  const confirmedFailure = ["CANCELLED", "REFUNDED", "CHARGEBACK"].includes(status);
  return (
    <div className="min-h-screen bg-brand-dark">
      {user?.role === "editor" ? <EditorNavbar /> : <UserNavbar />}
      <section className="max-w-md mx-auto px-6 py-24 text-center">
        <div className="flex justify-center mb-6"><Logo size={60} /></div>
        <div className="glass motion-panel rounded-2xl p-10">
          {confirmedFailure ? <XCircle className="text-red-400 mx-auto mb-4" size={56} /> : <Clock3 className="text-amber-400 mx-auto mb-4" size={56} />}
          <h1 className="font-display text-2xl font-bold mb-2">{confirmedFailure ? "Payment Not Completed" : "Payment Status Pending"}</h1>
          <p className="text-gray-400 text-sm mb-2">{message}</p>
          {orderId && <p className="text-xs text-gray-600 mb-8">Reference: {orderId}</p>}
          <div className="space-y-3">
            <PrimaryButton className="w-full" onClick={() => navigate(user?.role === "editor" ? "/editor/earnings" : "/order-history")}>
              {user?.role === "editor" ? "View Earnings" : "Return to Orders"}
            </PrimaryButton>
            <OutlineButton className="w-full" onClick={() => navigate(-1)}>Try Again</OutlineButton>
          </div>
        </div>
      </section>
    </div>
  );
}
