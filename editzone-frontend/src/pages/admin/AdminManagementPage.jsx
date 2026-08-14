import { useEffect, useMemo, useState } from "react";
import { BarChart3, RefreshCw, Search } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Badge, Loader } from "../../components/common/UI";
import api from "../../services/api";

const CONFIG = {
  requests: {
    title: "Request & Proposal Management", endpoint: "/admin/requests",
    description: "Track client requests, editor responses, and project progress.",
    columns: [["project_title", "Project"], ["status", "Status"], ["project_description", "Description"], ["created_at", "Created"]],
  },
  paymentProtection: {
    title: "Payment Protection", endpoint: "/admin/payment-protection",
    description: "Monitor protected authorizations, captured funds, and refunds.",
    columns: [["project_name", "Project"], ["status", "Status"], ["authorized_amount", "Protected Amount"], ["currency", "Currency"]],
  },
  disputes: {
    title: "Dispute & Complaint Management", endpoint: "/admin/disputes",
    description: "Review and resolve customer complaints and project disputes.",
    columns: [["subject", "Subject"], ["status", "Status"], ["category", "Category"], ["created_at", "Created"]],
  },
  "chat-reports": {
    title: "Chat Report Management", endpoint: "/admin/chat-reports",
    description: "Investigate reported conversations while preserving participant privacy.",
    columns: [["request_id", "Project Request"], ["reason", "Reason"], ["status", "Status"], ["created_at", "Reported"]],
  },
  reviews: {
    title: "Review Management", endpoint: "/admin/reviews",
    description: "Moderate marketplace ratings and customer feedback.",
    columns: [["request_id", "Project Request"], ["rating", "Rating"], ["comment", "Review"], ["created_at", "Created"]],
  },
  content: {
    title: "Content Management", endpoint: "/admin/content",
    description: "Manage the status of public EditZone pages and platform content.",
    columns: [["title", "Page"], ["slug", "Slug"], ["status", "Status"], ["updated_at", "Updated"]],
  },
};

function displayValue(key, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (key.includes("amount")) return Number(value).toLocaleString("en-LK", { minimumFractionDigits: 2 });
  if (key.endsWith("_at")) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
  }
  return String(value);
}

export default function AdminManagementPage({ type }) {
  const config = CONFIG[type];
  const [items, setItems] = useState(null);
  const [meta, setMeta] = useState({});
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  const load = () => {
    setItems(null);
    setError("");
    api.get(config.endpoint)
      .then((response) => {
        setItems(response.data.items || []);
        setMeta(response.data);
      })
      .catch((err) => {
        setItems([]);
        setError(err.response?.data?.message || `Unable to load ${config.title.toLowerCase()}`);
      });
  };

  useEffect(() => {
    load();
  }, [type]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items || [];
    return (items || []).filter((item) => Object.values(item).some((value) => String(value ?? "").toLowerCase().includes(needle)));
  }, [items, query]);

  return (
    <AdminLayout>
      <div className="mb-7 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-bold uppercase tracking-[.18em] text-brand-gold">Administration</p>
          <h1 className="mt-1 text-2xl font-bold">{config.title}</h1>
          <p className="mt-1 text-sm text-gray-400">{config.description}</p>
        </div>
        <div className="relative w-full sm:w-72">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter records…" className="w-full rounded-xl border border-brand-border bg-brand-panel px-4 py-2.5 pl-10 text-sm text-white outline-none" />
        </div>
      </div>

      <div className="mb-5 grid gap-4 sm:grid-cols-3">
        <div className="glass rounded-xl p-5"><p className="text-xs text-gray-500">Total records</p><p className="mt-1 text-2xl font-bold">{items?.length ?? "—"}</p></div>
        <div className="glass rounded-xl p-5"><p className="text-xs text-gray-500">Visible results</p><p className="mt-1 text-2xl font-bold">{items ? filtered.length : "—"}</p></div>
        <div className="glass rounded-xl p-5"><p className="text-xs text-gray-500">System status</p><p className="mt-1 text-lg font-bold text-emerald-400">Operational</p></div>
      </div>

      {error ? (
        <div className="glass rounded-2xl p-10 text-center"><p className="text-red-400">{error}</p><button onClick={load} className="mt-4 inline-flex items-center gap-2 text-brand-gold"><RefreshCw size={16} /> Try again</button></div>
      ) : !items ? <Loader /> : filtered.length === 0 ? (
        <div className="glass rounded-2xl p-12 text-center"><BarChart3 className="mx-auto text-brand-gold" size={34} /><h2 className="mt-3 font-semibold">No records found</h2><p className="mt-1 text-sm text-gray-500">{type === "chat-reports" && meta.message_count ? `${meta.message_count} normal messages exist, with no reported conversations.` : "Records will appear here when they become available."}</p></div>
      ) : (
        <div className="glass overflow-x-auto rounded-2xl">
          <table className="w-full min-w-[760px] text-sm">
            <thead><tr className="border-b border-brand-border text-left text-gray-400">{config.columns.map(([, label]) => <th key={label} className="p-4 font-semibold">{label}</th>)}</tr></thead>
            <tbody>{filtered.map((item, index) => (
              <tr key={item.id || index} className="border-b border-brand-border last:border-0">
                {config.columns.map(([key]) => <td key={key} className="max-w-sm truncate p-4 text-gray-300">{key === "status" ? <Badge tone={["CAPTURED", "completed", "published", "resolved"].includes(item[key]) ? "success" : "warning"}>{displayValue(key, item[key])}</Badge> : displayValue(key, item[key])}</td>)}
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </AdminLayout>
  );
}
