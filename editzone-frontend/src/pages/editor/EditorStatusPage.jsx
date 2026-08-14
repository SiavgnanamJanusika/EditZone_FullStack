import StatusManager from "../../components/status/StatusManager";

export default function EditorStatusPage() {
  return <section className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8"><header><p className="text-xs font-bold uppercase tracking-[.2em] text-brand-gold">Editor workspace</p><h1 className="mt-1 text-3xl font-bold">Status</h1><p className="mt-2 text-sm text-slate-400">Share your latest work with clients. Image and short-video statuses remain available for 24 hours.</p></header><StatusManager /></section>;
}
