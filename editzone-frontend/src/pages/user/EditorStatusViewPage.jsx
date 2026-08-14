import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import UserNavbar from "../../components/navbar/UserNavbar";
import StatusViewer from "../../components/status/StatusViewer";
import { Loader } from "../../components/common/UI";
import { statusApi } from "../../services/statuses";
import { useSocket } from "../../context/SocketContext";

export default function EditorStatusViewPage() {
  const { editorId, statusId } = useParams();
  const navigate = useNavigate(); const location = useLocation(); const { socket } = useSocket();
  const [statuses, setStatuses] = useState([]); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  const back = () => navigate(location.state?.from || `/editors/${editorId}`);
  useEffect(() => { let live = true; statusApi.forEditor(editorId).then(({ data }) => { if (live) setStatuses(data.statuses || []); }).catch((err) => { if (live) setError(err.response?.status === 401 ? "Authentication expired" : "Status is temporarily unavailable"); }).finally(() => live && setLoading(false)); return () => { live = false; }; }, [editorId]);
  useEffect(() => { if (!socket) return undefined; const liked = (data) => setStatuses((items) => items.map((item) => item.id === data.status_id ? { ...item, ...data } : item)); const deleted = (data) => setStatuses((items) => items.filter((item) => item.id !== data.status_id)); socket.on("status_like_updated", liked); socket.on("status_deleted", deleted); return () => { socket.off("status_like_updated", liked); socket.off("status_deleted", deleted); }; }, [socket]);
  const initialIndex = Math.max(0, statuses.findIndex((item) => item.id === statusId));
  const group = useMemo(() => statuses.length ? { editor: statuses[0].editor, statuses } : null, [statuses]);
  return <div className="min-h-dvh bg-brand-dark text-white"><UserNavbar />{loading ? <Loader label="Loading status…" /> : error || !group ? <main className="mx-auto max-w-xl px-4 py-24 text-center"><h1 className="text-xl font-bold">Status unavailable</h1><p className="mt-2 text-red-200">{error || "Status was deleted or has expired"}</p><button onClick={back} className="mt-5 rounded-xl border border-white/10 px-4 py-2">Back</button></main> : <StatusViewer group={group} initialIndex={initialIndex} onClose={back} onChange={(changed) => setStatuses((items) => items.map((item) => item.id === changed.id ? changed : item))} onDeleted={(id) => setStatuses((items) => items.filter((item) => item.id !== id))} />}</div>;
}
