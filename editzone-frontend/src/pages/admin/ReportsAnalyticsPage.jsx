import { useEffect, useState } from "react";
import { BarChart3, Banknote, CheckCircle2, FolderKanban, Users } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Loader } from "../../components/common/UI";
import api from "../../services/api";

export default function ReportsAnalyticsPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get("/admin/analytics").then((response) => setData(response.data)).catch((err) => setError(err.response?.data?.message || "Unable to load analytics"));
  }, []);
  const summary = data?.summary;
  const cards = summary ? [
    [Users, "Total users", summary.total_users + summary.total_editors],
    [FolderKanban, "Projects", summary.total_projects],
    [CheckCircle2, "Completed", summary.completed_projects],
    [Banknote, "Revenue", `Rs. ${Number(summary.total_revenue || 0).toLocaleString("en-LK")}`],
  ] : [];
  return <AdminLayout>
    <div className="mb-7"><p className="text-xs font-bold uppercase tracking-[.18em] text-brand-gold">Business intelligence</p><h1 className="mt-1 text-2xl font-bold">Reports & Analytics</h1><p className="mt-1 text-sm text-gray-400">Marketplace performance, payment, and project insights.</p></div>
    {error ? <p className="py-12 text-center text-red-400">{error}</p> : !data ? <Loader /> : <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{cards.map(([Icon, label, value]) => <div key={label} className="glass rounded-xl p-5"><Icon className="text-brand-gold" size={21} /><p className="mt-4 text-2xl font-bold">{value}</p><p className="text-sm text-gray-500">{label}</p></div>)}</div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        {[["Project status", data.project_statuses], ["Payment status", data.payment_statuses]].map(([title, rows]) => <div key={title} className="glass rounded-2xl p-6"><h2 className="flex items-center gap-2 font-semibold"><BarChart3 size={18} className="text-brand-gold" />{title}</h2><div className="mt-5 space-y-3">{rows.map((row) => <div key={row.status}><div className="mb-1 flex justify-between text-sm"><span className="capitalize text-gray-400">{row.status || "Unknown"}</span><span>{row.count}</span></div><div className="h-2 overflow-hidden rounded-full bg-white/5"><div className="h-full rounded-full bg-brand-gradient" style={{ width: `${Math.max(8, Math.min(100, row.count * 10))}%` }} /></div></div>)}</div></div>)}
      </div>
    </>}
  </AdminLayout>;
}
