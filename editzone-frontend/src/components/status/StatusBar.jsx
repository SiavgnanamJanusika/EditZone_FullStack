import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Loader } from "../common/UI";
import { groupStatuses, statusApi } from "../../services/statuses";
import StatusAvatar from "./StatusAvatar";

export default function StatusBar({ className = "" }) {
  const navigate = useNavigate(); const location = useLocation();
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => { statusApi.list().then(({ data }) => setGroups(groupStatuses(data.statuses))).catch(() => setGroups([])).finally(() => setLoading(false)); }, []);
  if (!loading && !groups.length) return null;
  return <section aria-label="Editor Status" className={`glass mb-8 rounded-2xl p-4 sm:p-5 ${className}`}><div className="mb-4 flex items-center justify-between"><div><h2 className="font-display text-lg font-bold">Editor Status</h2><p className="text-xs text-gray-500">Fresh work and updates from editors</p></div></div>{loading ? <Loader label="Loading statuses…" /> : <div className="flex gap-4 overflow-x-auto pb-1">{groups.map((group) => { const viewed = group.statuses.every((item) => item.is_viewed_by_me); const editorId = group.editor.profile_id || group.editor.id; return <div key={group.editor.id} className="w-20 shrink-0 text-center"><StatusAvatar editor={group.editor} viewed={viewed} onClick={() => navigate(`/editors/${editorId}/status/${group.statuses[0].id}`, { state: { from: location.pathname + location.search } })} /><p className="mt-1.5 truncate text-xs text-gray-300">{group.editor.name}</p></div>; })}</div>}</section>;
}
