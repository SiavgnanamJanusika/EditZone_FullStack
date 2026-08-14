import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CheckCircle2, ExternalLink, ShieldCheck } from "lucide-react";
import UserNavbar from "../../components/navbar/UserNavbar";
import { ErrorText, Loader, OutlineButton, PrimaryButton, Badge } from "../../components/common/UI";
import api from "../../services/api";
import { protectedMediaUrl } from "../../services/media";

export default function ApproveWorkPage() {
  const { requestId } = useParams();
  const navigate = useNavigate();
  const [request, setRequest] = useState(null);
  const [payment, setPayment] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");

  useEffect(() => {
    Promise.all([
      api.get(`/requests/${requestId}`),
      api.get(`/payments/status/${requestId}`),
    ])
      .then(([requestResponse, paymentResponse]) => {
        setRequest(requestResponse.data);
        setPayment(paymentResponse.data);
        if (requestResponse.data?.delivered_file_url) {
          protectedMediaUrl(requestResponse.data.delivered_file_url).then(setPreviewUrl).catch(() => setError("The protected final preview could not be opened"));
        }
      })
      .catch((err) => setError(err.response?.data?.message || "Unable to load approval details"));
  }, [requestId]);

  const approve = async () => {
    setSubmitting(true);
    setError("");
    try {
      const response = await api.post(`/payments/${requestId}/approve`);
      setPayment(response.data.payment);
    } catch (err) {
      setError(err.response?.data?.message || "Payment capture failed");
    } finally {
      setSubmitting(false);
    }
  };

  if (!request && !error) return <div className="min-h-screen bg-brand-dark"><UserNavbar /><Loader /></div>;
  const captured = payment?.status === "CAPTURED";

  return (
    <div className="min-h-screen bg-brand-dark">
      <UserNavbar />
      <section className="max-w-2xl mx-auto px-6 py-10">
        <div className="glass motion-panel rounded-2xl p-8">
          <div className="flex items-start justify-between gap-4 mb-6">
            <div>
              <p className="text-brand-gold text-xs uppercase tracking-widest mb-2">Protected Until Client Approval</p>
              <h1 className="font-display text-2xl font-bold">Approve Final Video</h1>
              <p className="text-gray-400 text-sm mt-1">{request?.project_title}</p>
            </div>
            {payment && <Badge tone={captured ? "success" : "warning"}>{payment.status}</Badge>}
          </div>

          {request?.delivered_file_url ? (
            <a
              href={previewUrl || undefined}
              target="_blank"
              rel="noreferrer"
              className="flex items-center justify-between rounded-xl border border-brand-border bg-brand-panel p-4 text-brand-gold hover:border-brand-goldLight"
            >
              Review final delivered work <ExternalLink size={18} />
            </a>
          ) : (
            <p className="rounded-xl border border-brand-gold/30 bg-brand-gold/10 p-4 text-sm text-brand-goldLight">
              The editor has not uploaded final work yet.
            </p>
          )}

          <div className="my-6 rounded-xl border border-white/10 p-4 text-sm">
            <div className="flex justify-between"><span className="text-gray-400">Authorized amount</span><span>{payment?.currency} {Number(payment?.authorized_amount || 0).toFixed(2)}</span></div>
            <div className="flex justify-between mt-2"><span className="text-gray-400">Payment status</span><span>{payment?.status}</span></div>
          </div>

          {!captured && request?.delivered_file_url && <label className="mb-5 flex items-start gap-3 text-sm text-gray-300"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1" />I reviewed the admin-verified final video and authorize release of the protected payment.</label>}

          <ErrorText>{error}</ErrorText>
          {captured ? (
            <div className="rounded-xl bg-green-500/10 border border-green-500/30 p-5 text-center">
              <CheckCircle2 className="text-green-400 mx-auto mb-2" />
              <p className="font-semibold">Work approved and payment captured</p>
              <p className="text-xs text-gray-400 mt-1">PayHere settles captured merchant funds to the bank account registered on the merchant account.</p>
            </div>
          ) : (
            <PrimaryButton className="w-full flex items-center justify-center gap-2" disabled={!confirmed || submitting || payment?.status !== "AUTHORIZED" || request?.status !== "delivered"} onClick={approve}><ShieldCheck size={18} /> {submitting ? "Releasing..." : "Approve Video & Release Payment"}</PrimaryButton>
          )}
          <OutlineButton className="w-full mt-3" onClick={() => navigate("/order-history")}>Back to Orders</OutlineButton>
        </div>
      </section>
    </div>
  );
}
