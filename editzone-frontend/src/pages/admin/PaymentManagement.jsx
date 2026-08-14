import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Loader, Badge, OutlineButton } from "../../components/common/UI";
import { CheckCircle2 } from "lucide-react";
import api from "../../services/api";

export default function PaymentManagement() {
  const [payments, setPayments] = useState(null);
  const [error, setError] = useState("");
  const [payouts, setPayouts] = useState([]);
  const [busyId, setBusyId] = useState(null);

  const loadPayments = () => {
    setError("");
    return Promise.all([api.get("/admin/payments"), api.get("/admin/editor-payouts")])
      .then(([paymentResponse, payoutResponse]) => { setPayments(paymentResponse.data.payments); setPayouts(payoutResponse.data.payouts || []); })
      .catch((err) => setError(err.response?.data?.message || "Unable to load payments"));
  };

  useEffect(() => { loadPayments(); }, []);

  const recordPaid = async (payout) => {
    const reference = window.prompt("Enter the real bank/payment reference. This does not send money through PayHere:");
    if (!reference?.trim()) return;
    setBusyId(payout.id);
    try {
      await api.patch(`/admin/editor-payouts/${payout.id}`, { status: "PAID", reference: reference.trim() });
      await loadPayments();
    } catch (err) {
      setError(err.response?.data?.message || "Unable to record editor payout");
    } finally { setBusyId(null); }
  };

  return (
    <AdminLayout>
      <h1 className="font-display text-2xl font-bold mb-6">Payment Management</h1>
      <section className="mb-6 rounded-2xl border border-brand-gold/20 bg-brand-gold/5 p-5">
        <div className="flex flex-wrap items-center gap-3"><span className="rounded-full bg-brand-gold/15 px-3 py-1 text-xs font-bold text-brand-goldLight">PAYHERE SANDBOX DEMO</span><span className="text-xs text-gray-400">Free · No real payments · API integrated</span></div>
        <div className="mt-4 grid gap-2 text-xs text-gray-300 sm:grid-cols-2 lg:grid-cols-4">{["Success & failure", "Card authorization", "Signed callback", "Refund API", "Status updates", "Client approval", "Admin monitoring", "Project demo ready"].map((item) => <span key={item} className="flex items-center gap-2"><CheckCircle2 size={14} className="text-emerald-400" />{item}</span>)}</div>
      </section>
      {error ? (
        <p className="py-12 text-center text-red-400">{error}</p>
      ) : !payments ? (
        <Loader />
      ) : (
        <div className="glass rounded-xl overflow-hidden overflow-x-auto">
          <table className="w-full text-sm min-w-[700px]">
            <thead>
              <tr className="text-left text-gray-400 border-b border-brand-border">
                <th className="p-4">Project</th>
                <th className="p-4">Amount</th>
                <th className="p-4">Platform Fee</th>
                <th className="p-4">Editor Earning</th>
                <th className="p-4">Method</th>
                <th className="p-4">Payment Protection</th>
              </tr>
            </thead>
            <tbody>
              {payments.map((p) => (
                <tr key={p.id} className="border-b border-brand-border last:border-0">
                  <td className="p-4 text-white">{p.project_name}</td>
                  <td className="p-4 text-gray-300">{p.currency || "LKR"} {p.amount}</td>
                  <td className="p-4 text-gray-400">{p.currency || "LKR"} {p.platform_fee_amount ?? p.commission_amount ?? 0}</td>
                  <td className="p-4 text-gray-400">{p.currency || "LKR"} {p.editor_earning_amount ?? p.editor_payout_amount ?? 0}</td>
                  <td className="p-4 text-gray-400 capitalize">{p.payment_method}</td>
                  <td className="p-4">
                    <Badge tone={p.protection_status === "RELEASED" ? "success" : p.protection_status === "REFUNDED" || p.status === "CANCELLED" ? "danger" : "warning"}>{p.protection_status || p.status}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h2 className="mb-4 mt-8 font-display text-xl font-bold">Editor Settlement Ledger</h2>
      <div className="grid gap-4 lg:grid-cols-2">
        {payouts.map((payout) => <article key={payout.id} className="glass rounded-xl p-5">
          <div className="flex items-start justify-between gap-3"><div><p className="text-xs uppercase tracking-widest text-brand-gold">Settlement Breakdown</p><p className="mt-1 font-semibold text-white">{payout.editor_name || "Editor"} · {payout.month || "Legacy payout"}</p><p className="text-xs text-slate-500">{payout.payment_destination_summary || "Bank destination unavailable"}</p><p className="text-xs text-slate-500">Order {payout.order_id}</p></div><Badge tone={payout.payout_status === "PAID" ? "success" : payout.payout_status === "FAILED" ? "danger" : "warning"}>{payout.payout_status}</Badge></div>
          <dl className="mt-4 space-y-2 text-sm"><div className="flex justify-between"><dt className="text-gray-400">Gross project earnings</dt><dd>LKR {Number(payout.gross_amount ?? (payout.gross_amount_minor || 0) / 100).toFixed(2)}</dd></div><div className="flex justify-between"><dt className="text-gray-400">Editor commission 10%</dt><dd>LKR {Number(payout.platform_commission ?? (payout.editor_commission_minor || 0) / 100).toFixed(2)}</dd></div><div className="flex justify-between border-t border-white/10 pt-2"><dt className="text-gray-300">Editor payable 90%</dt><dd className="text-white">LKR {Number(payout.editor_net ?? (payout.net_amount_minor || 0) / 100).toFixed(2)}</dd></div></dl>
          {payout.payout_status !== "PAID" && <OutlineButton className="mt-4 w-full" disabled={busyId === payout.id || payout.payout_eligible === false} onClick={() => recordPaid(payout)}>{payout.payout_eligible === false ? "Awaiting project release" : busyId === payout.id ? "Recording…" : "Record Manual Payout"}</OutlineButton>}
          {payout.payout_status === "PAID" && <p className="mt-3 text-xs text-gray-500">Recorded by admin · Reference: {payout.payout_reference}</p>}
        </article>)}
        {payments && payouts.length === 0 && <p className="text-sm text-gray-500">No editor payouts are ready yet.</p>}
      </div>
    </AdminLayout>
  );
}
