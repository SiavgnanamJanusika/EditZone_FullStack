import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Check, CreditCard, LockKeyhole, ShieldCheck } from "lucide-react";
import UserNavbar from "../../components/navbar/UserNavbar";
import { ErrorText, Input, Loader, Logo, PrimaryButton } from "../../components/common/UI";
import api from "../../services/api";

export default function PaymentPage() {
  const { requestId, quoteId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [request, setRequest] = useState(null);
  const [quote, setQuote] = useState(null);
  const [editor, setEditor] = useState(null);
  const [form, setForm] = useState({ address: "", city: "" });
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  useEffect(() => {
    const load = quoteId
      ? api.get(`/quotes/${encodeURIComponent(quoteId)}`).then(async ({ data }) => {
        const participant = await api.get(`/chat/${data.quote.project_id}/participant`);
        return [data.project, data.quote, participant.data];
      })
      : Promise.all([api.get(`/requests/${requestId}`), api.get(`/projects/${requestId}/final-quote`), api.get(`/chat/${requestId}/participant`)]).then(([project, quoteResult, participant]) => [project.data, quoteResult.data.quote, participant.data]);
    load.then(([project, loadedQuote, participant]) => {
        setRequest(project); setQuote(loadedQuote); setEditor(participant);
      })
      .catch((err) => setError(err.response?.data?.detail || "Checkout could not be loaded"))
      .finally(() => setLoading(false));
  }, [requestId, quoteId]);

  const submit = async (event) => {
    event.preventDefault();
    if (submitting || !quote) return;
    setSubmitting(true); setError("");
    try {
      const { data } = await api.post("/payments/payhere/create", { quote_id: quote.id, ...form });
      const checkoutUrl = data.checkout_url || data.action_url;
      const paymentData = data.payment_data || data.fields;
      if (!checkoutUrl || !paymentData?.hash) throw new Error("The payment gateway returned an incomplete checkout request.");
      const checkoutForm = document.createElement("form");
      checkoutForm.method = "POST"; checkoutForm.action = checkoutUrl;
      Object.entries(paymentData).forEach(([name, value]) => { if (value === undefined || value === null) return; const input = document.createElement("input"); input.type = "hidden"; input.name = name; input.value = String(value); checkoutForm.appendChild(input); });
      document.body.appendChild(checkoutForm); checkoutForm.submit();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Unable to start payment. Please try again."); setSubmitting(false);
    }
  };

  if (loading) return <div className="min-h-dvh bg-brand-dark"><UserNavbar /><Loader label="Loading secure checkout…" /></div>;
  const projectId = quote?.project_id || requestId;
  const blocked = !quote || quote.status !== "SENT";
  return <div className="min-h-dvh bg-brand-dark text-white"><UserNavbar /><main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
    <header className="mb-7 flex flex-wrap items-center justify-between gap-4"><div className="flex items-center gap-3"><Logo size={54} /><div><h1 className="text-xl font-bold">Secure Checkout</h1><p className="text-xs text-slate-400">Hosted payment by PayHere Sandbox</p></div></div><ol className="flex gap-2 text-xs">{["Review", "Payment", "Completed"].map((step, index) => <li key={step} className={`rounded-full px-3 py-2 ${index < 2 ? "bg-brand-gold/15 text-brand-goldLight" : "bg-white/5 text-slate-500"}`}>{index + 1}. {step}</li>)}</ol></header>
    <button type="button" onClick={() => navigate(`/chat/${projectId}`)} className="mb-5 flex items-center gap-2 text-sm text-slate-400 hover:text-brand-gold"><ArrowLeft size={16} /> Back to Chat</button>
    <div className="grid gap-6 lg:grid-cols-[.9fr_1.1fr]">
      <section className="glass rounded-2xl p-6"><p className="text-xs font-bold uppercase tracking-[.18em] text-brand-gold">Order summary</p><h2 className="mt-2 text-2xl font-bold">{request?.project_title || "EditZone project"}</h2><div className="mt-5 flex items-center gap-3"><span className="grid h-12 w-12 place-items-center overflow-hidden rounded-full bg-avatar-gradient font-bold text-brand-gold">{editor?.profile_picture ? <img src={editor.profile_picture} alt="" className="h-full w-full object-cover" /> : (editor?.display_name || "ED").slice(0, 2).toUpperCase()}</span><div><p className="font-semibold">{editor?.display_name || "Assigned editor"}</p><p className="text-xs text-slate-500">Project ID · {requestId}</p></div></div>
        {quote ? <dl className="mt-6 space-y-3 text-sm"><div className="flex justify-between"><dt>Project amount</dt><dd>LKR {quote.project_amount}</dd></div><div className="flex justify-between text-slate-400"><dt>Client service fee (10%)</dt><dd>LKR {quote.client_service_fee}</dd></div><div className="flex justify-between border-t border-white/10 pt-4 text-xl font-bold"><dt>Total payable</dt><dd>Rs {quote.client_total}</dd></div><div className="flex justify-between text-xs text-slate-500"><dt>Currency</dt><dd>{quote.currency}</dd></div><div className="flex justify-between text-xs text-slate-500"><dt>Quote ID</dt><dd>{quote.id}</dd></div><div className="flex justify-between text-xs text-slate-500"><dt>Expires</dt><dd>{quote.expires_at ? new Date(quote.expires_at).toLocaleString("en-LK", { timeZone: "Asia/Colombo" }) : "Not specified"}</dd></div></dl> : <p className="mt-6 text-amber-300">The editor has not set a final amount.</p>}
        <p className="mt-5 flex items-center gap-2 rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-3 text-xs text-emerald-200"><ShieldCheck size={17} /> Secure PayHere hosted payment · LKR</p>
      </section>
      <section className="glass rounded-2xl p-6"><div className="flex items-center gap-2"><CreditCard className="text-brand-gold" /><h2 className="text-xl font-bold">Payment details</h2></div><p className="mt-2 text-sm text-slate-400">Card details are entered only on PayHere. EditZone never receives or stores them.</p>
        <div className="mt-5 grid gap-3 rounded-xl border border-white/10 bg-white/[.025] p-4 text-sm sm:grid-cols-2"><p><span className="block text-xs text-slate-500">Client</span>{request?.client_name || "Signed-in client"}</p><p><span className="block text-xs text-slate-500">Phone</span>{request?.client_phone || "From your verified profile"}</p><p><span className="block text-xs text-slate-500">Payment method</span>PayHere Sandbox</p></div>
        <form onSubmit={submit} className="mt-5 space-y-4"><Input name="address" autoComplete="street-address" placeholder="Billing address" required minLength={3} maxLength={200} value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /><Input name="city" autoComplete="address-level2" placeholder="City" required minLength={2} maxLength={100} value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} /><label className="flex items-start gap-3 text-sm text-slate-300"><input type="checkbox" required checked={acceptedTerms} onChange={(e) => setAcceptedTerms(e.target.checked)} className="mt-1 accent-amber-400" /><span>I agree to the payment terms and understand that PayHere securely processes this sandbox checkout.</span></label><ErrorText>{error || (quote?.status === "EXPIRED" ? "This payment request has expired. Return to chat for a new request." : quote?.status === "PAYMENT_PENDING" ? "A payment attempt is already pending. Check its status before retrying." : "")}</ErrorText><PrimaryButton type="submit" className="w-full" disabled={submitting || blocked || !acceptedTerms}>{submitting ? "Preparing payment…" : "Continue with PayHere"}</PrimaryButton><button type="button" onClick={() => navigate(`/chat/${projectId}`)} className="w-full rounded-xl border border-white/10 px-4 py-3 text-sm">Back to Chat</button></form>
      </section>
    </div>
    <section className="mt-6 rounded-2xl border border-white/10 bg-white/[.025] p-6"><h2 className="flex items-center gap-2 font-bold"><LockKeyhole className="text-brand-gold" size={19} /> How your payment works</h2><ol className="mt-4 grid gap-3 text-sm text-slate-400 md:grid-cols-2">{["The editor sets and locks the project price.", "EditZone adds a clearly displayed 10% client service fee.", "PayHere securely processes the total payment.", "EditZone verifies the signed backend callback.", "Project and earnings update only after verification.", "The editor net payout records a 10% commission deduction.", "Refunds and chargebacks remain in both ledgers."].map((item, i) => <li key={item} className="flex gap-2"><Check className="mt-0.5 shrink-0 text-emerald-400" size={15} /><span>{i + 1}. {item}</span></li>)}</ol></section>
  </main></div>;
}
