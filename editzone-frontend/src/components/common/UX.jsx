/* eslint-disable react-refresh/only-export-components */
import { Component, createContext, useCallback, useContext, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";

const ToastContext = createContext(null);
let publishToast = null;

export function toast(message, tone = "info") {
  publishToast?.({ message, tone, id: `${Date.now()}-${Math.random()}` });
}

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const publish = useCallback((item) => setItems((current) => [...current, item].slice(-4)), []);

  useEffect(() => {
    publishToast = publish;
    return () => { publishToast = null; };
  }, [publish]);

  const dismiss = (id) => setItems((current) => current.filter((item) => item.id !== id));

  return (
    <ToastContext.Provider value={publish}>
      {children}
      <div className="fixed right-3 top-3 z-[100] flex w-[calc(100%-1.5rem)] max-w-sm flex-col gap-2 sm:right-5 sm:top-5" aria-live="polite" aria-atomic="true">
        {items.map((item) => <Toast key={item.id} item={item} dismiss={dismiss} />)}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

function Toast({ item, dismiss }) {
  const icons = { success: CheckCircle2, error: XCircle, warning: AlertTriangle, info: Info };
  const Icon = icons[item.tone] || Info;
  useEffect(() => {
    const timer = window.setTimeout(() => dismiss(item.id), 4500);
    return () => { window.clearTimeout(timer); };
  }, [dismiss, item.id]);
  return (
    <div className={`toast toast-${item.tone}`} role={item.tone === "error" ? "alert" : "status"}>
      <Icon size={19} className="shrink-0" aria-hidden="true" />
      <p className="flex-1 text-sm font-medium">{item.message}</p>
      <button type="button" onClick={() => dismiss(item.id)} aria-label="Dismiss notification" className="rounded-full p-1 hover:bg-white/10"><X size={16} /></button>
    </div>
  );
}

export function ConfirmModal({ open, title, description, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false, busy = false, onConfirm, onCancel }) {
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => event.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("keydown", close); };
  }, [open, busy, onCancel]);
  if (!open) return null;
  return (
    <div className="modal-backdrop fixed inset-0 z-[90] grid place-items-center bg-black/70 p-4 backdrop-blur-sm" onMouseDown={(event) => event.target === event.currentTarget && !busy && onCancel()}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description" className="modal-panel glass w-full max-w-md rounded-2xl p-6 shadow-2xl">
        <div className={`grid h-11 w-11 place-items-center rounded-xl ${danger ? "bg-red-500/10 text-red-300" : "bg-brand-gold/15 text-brand-gold"}`}><AlertTriangle size={22} /></div>
        <h2 id="confirm-title" className="mt-4 font-display text-xl font-semibold">{title}</h2>
        <p id="confirm-description" className="mt-2 text-sm leading-6 text-gray-400">{description}</p>
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" onClick={onCancel} disabled={busy} className="btn-secondary">{cancelLabel}</button>
          <button type="button" onClick={onConfirm} disabled={busy} className={danger ? "btn-danger" : "btn-primary"}>{busy ? "Please wait..." : confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error) {
    console.error("EditZone UI error", error);
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="grid min-h-screen place-items-center bg-brand-dark px-6 text-center text-white">
        <div className="glass max-w-lg rounded-3xl p-10">
          <AlertTriangle size={42} className="mx-auto text-amber-300" />
          <h1 className="mt-5 font-display text-2xl font-bold">Something went wrong</h1>
          <p className="mt-2 text-sm text-gray-400">The page could not be displayed. Your data is safe; try rendering this view again.</p>
          <button type="button" onClick={() => this.setState({ failed: false })} className="btn-primary mt-6">Try again</button>
        </div>
      </main>
    );
  }
}
