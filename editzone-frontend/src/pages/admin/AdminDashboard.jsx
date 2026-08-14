import { useEffect, useState } from "react";
import { Users, Clapperboard, FolderKanban, Banknote, Clock, CheckCircle2, Inbox, PlayCircle, ArrowRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";
import { Loader } from "../../components/common/UI";
import api from "../../services/api";

export default function AdminDashboard() {
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get("/admin/dashboard-stats")
      .then((res) => setStats(res.data))
      .catch((err) => setError(err.response?.data?.message || "Unable to load dashboard statistics"));
  }, []);

  const cards = stats && [
    { icon: Users, label: "Total Clients", value: stats.total_users },
    { icon: Clapperboard, label: "Total Editors", value: stats.total_editors },
    { icon: FolderKanban, label: "Total Projects", value: stats.total_projects },
    { icon: Clock, label: "Pending Verification", value: stats.pending_verification },
    { icon: Banknote, label: "Total Revenue", value: `Rs. ${Number(stats.total_revenue || 0).toLocaleString("en-LK")}` },
    { icon: Banknote, label: "Platform Commission", value: `Rs. ${Number(stats.total_platform_commission || 0).toLocaleString("en-LK")}` },
  ];
  const workflowCards = stats && [
    { icon: Inbox, label: "New Requests", value: stats.new_requests, filter: "pending", tone: "text-brand-goldLight bg-brand-gold/10" },
    { icon: CheckCircle2, label: "Accepted", value: stats.accepted_projects, filter: "accepted", tone: "text-brand-gold bg-brand-gold/10" },
    { icon: PlayCircle, label: "Active Projects", value: stats.active_projects, filter: "active", tone: "text-amber-300 bg-amber-400/10" },
    { icon: CheckCircle2, label: "Completed", value: stats.completed_projects, filter: "completed", tone: "text-emerald-300 bg-emerald-400/10" },
  ];

  return (
    <AdminLayout>
      <h1 className="font-display text-2xl font-bold mb-6">Admin Overview</h1>
      {error ? (
        <p className="py-12 text-center text-red-400">{error}</p>
      ) : !stats ? (
        <Loader />
      ) : (
        <>
          <section className="mb-8">
            <div className="mb-4"><p className="text-xs font-bold uppercase tracking-[.18em] text-brand-gold">Project Workflow</p><h2 className="mt-1 text-xl font-semibold">Project status overview</h2></div>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {workflowCards.map(({ icon: Icon, label, value, filter, tone }) => (
                <button type="button" key={label} onClick={() => navigate(`/admin/projects?status=${filter}`)} className="glass motion-panel group rounded-2xl p-5 text-left">
                  <div className="flex items-start justify-between"><span className={`rounded-xl p-2.5 ${tone}`}><Icon size={21} /></span><ArrowRight size={17} className="text-gray-600 transition group-hover:translate-x-1 group-hover:text-brand-gold" /></div>
                  <p className="mt-5 text-3xl font-bold text-white">{value}</p>
                  <p className="mt-1 text-sm text-gray-400">{label}</p>
                </button>
              ))}
            </div>
          </section>
          <section>
            <div className="mb-4"><p className="text-xs font-bold uppercase tracking-[.18em] text-gray-500">General Statistics</p><h2 className="mt-1 text-xl font-semibold">Platform overview</h2></div>
            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {cards.map(({ icon: Icon, label, value }) => (
                <div key={label} className="glass rounded-xl p-6">
                  <Icon className="mb-3 text-brand-gold" size={24} />
                  <p className="text-2xl font-bold text-white">{value}</p>
                  <p className="text-sm text-gray-400">{label}</p>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </AdminLayout>
  );
}
