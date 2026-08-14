import { useCallback, useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Loader, Badge } from "../../components/common/UI";
import api from "../../services/api";
import AdminAccountActionModal from "../../components/admin/AdminAccountActionModal";

export default function UserManagement() {
  const [users, setUsers] = useState(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("active");
  const [selected, setSelected] = useState(null);
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    setError("");
    return api.get(`/admin/users?status=${filter}`)
      .then((res) => setUsers(res.data.users))
      .catch((err) => setError(err.response?.data?.message || "Unable to load users"));
  }, [filter]);
  useEffect(() => { load(); }, [load]);

  const toggleBan = async (id, isBanned) => {
    try {
      await api.patch(`/admin/users/${id}/ban`, { is_banned: !isBanned });
      await load();
    } catch (err) {
      setError(err.response?.data?.message || "Unable to update this user");
    }
  };

  return (
    <AdminLayout>
      <h1 className="font-display text-2xl font-bold mb-6">User Management</h1>
      <div className="mb-5 flex flex-wrap gap-2">{["active", "suspended", "deleted"].map((value) => <button key={value} onClick={() => setFilter(value)} className={`rounded-full px-4 py-2 text-sm capitalize ${filter === value ? "bg-brand-gold text-black" : "bg-white/5 text-gray-300"}`}>{value}</button>)}</div>
      {notice && <p className="mb-4 rounded-xl border border-emerald-400/30 bg-emerald-500/10 p-3 text-sm text-emerald-200">{notice}</p>}
      {error ? (
        <p className="py-12 text-center text-red-400">{error}</p>
      ) : !users ? (
        <Loader />
      ) : (
        <div className="table-responsive glass">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-400 border-b border-brand-border">
                <th className="p-4">Username</th>
                <th className="p-4">Email</th>
                <th className="p-4">District</th>
                <th className="p-4">Role</th>
                <th className="p-4">Registered</th>
                <th className="p-4">Status</th>
                <th className="p-4">Action</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-brand-border last:border-0">
                  <td className="p-4 text-white">{u.username}</td>
                  <td className="p-4 text-gray-400">{u.email}</td>
                  <td className="p-4 text-gray-400">{u.district || "-"}</td>
                  <td className="p-4 capitalize text-gray-400">{u.role}</td>
                  <td className="p-4 text-gray-400">{u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}</td>
                  <td className="p-4">
                    <Badge tone={u.is_deleted ? "danger" : u.is_banned ? "warning" : "success"}>{u.is_deleted ? "Deleted" : u.is_banned ? "Suspended" : "Active"}</Badge>
                  </td>
                  <td className="p-4">
                    {u.is_deleted ? <button onClick={() => setSelected({ account: u, action: "restore" })} className="text-xs rounded-lg border border-amber-400/40 px-3 py-1.5 text-amber-200">Restore Account</button> : <><button
                      onClick={() => toggleBan(u.id, u.is_banned)}
                      className="text-xs px-3 py-1.5 rounded-lg border border-brand-border text-brand-gold hover:border-brand-goldLight"
                    >
                      {u.is_banned ? "Unban" : "Ban"}
                    </button><button onClick={() => setSelected({ account: u, action: "delete" })} className="ml-2 text-xs rounded-lg border border-red-400/40 px-3 py-1.5 text-red-300">Delete Account</button></>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selected && <AdminAccountActionModal account={selected.account} action={selected.action} onClose={() => setSelected(null)} onSuccess={(message) => { setSelected(null); setNotice(message); load(); }} />}
    </AdminLayout>
  );
}
