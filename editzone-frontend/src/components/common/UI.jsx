import { Inbox } from "lucide-react";
import logoImg from "../../assets/editzone-logo.png";

export function Logo({ size = 40, withText = false, premium = false }) {
  return (
    <div className="component-logo flex items-center gap-2">
      <img src={logoImg} alt="EditZone" style={{ height: size, width: "auto" }} className="rounded-md" />
      {withText && (
        <span className={premium ? "brand-wordmark font-display text-lg font-bold tracking-wide" : "font-display text-lg font-bold tracking-wide bg-gradient-to-r from-brand-gold to-brand-goldLight bg-clip-text text-transparent"}>
          {premium ? <><span>Edit</span><span>Zone</span></> : "EditZone"}
        </span>
      )}
    </div>
  );
}

export function OrbitLogo({ size = 112, logoSize = Math.round(size * 0.58), className = "" }) {
  return (
    <div className={`orbit-logo ${className}`} style={{ "--orbit-size": `${size}px` }}>
      <span className="orbit-ambient" aria-hidden="true" />
      <span className="orbit-ring orbit-ring-outer" aria-hidden="true" />
      <span className="orbit-ring orbit-ring-middle" aria-hidden="true" />
      <span className="orbit-ring orbit-ring-inner" aria-hidden="true" />
      <span className="orbit-satellite orbit-satellite-one" aria-hidden="true" />
      <span className="orbit-satellite orbit-satellite-two" aria-hidden="true" />
      <div className="orbit-logo-mark"><Logo size={logoSize} /></div>
    </div>
  );
}

export function PrimaryButton({ children, className = "", ...props }) {
  return (
    <button
      className={`component-button component-button-primary px-5 py-2.5 rounded-lg font-semibold text-white bg-brand-gradient disabled:opacity-50 disabled:cursor-not-allowed ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function OutlineButton({ children, className = "", ...props }) {
  return (
    <button
      className={`component-button component-button-outline px-5 py-2.5 rounded-lg font-semibold text-brand-gold border border-brand-border disabled:opacity-50 ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function Skeleton({ className = "" }) {
  return <span aria-hidden="true" className={`skeleton block rounded-lg ${className}`} />;
}

export function Loader({ label = "Loading..." }) {
  return (
    <div role="status" aria-live="polite" className="mx-auto w-full max-w-3xl space-y-4 px-4 py-10">
      <span className="sr-only">{label}</span>
      <Skeleton className="h-7 w-44" />
      <Skeleton className="h-24 w-full" />
      <div className="grid grid-cols-2 gap-4">
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
    </div>
  );
}

export function EmptyState({ icon: Icon = Inbox, title = "Nothing here yet", description, action }) {
  return (
    <div className="empty-state component-empty glass rounded-2xl p-10 text-center">
      <div className="component-empty-icon mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-brand-gold/10 text-brand-gold"><Icon size={30} aria-hidden="true" /></div>
      <h2 className="mt-4 font-display text-lg font-semibold text-white">{title}</h2>
      {description && <p className="mx-auto mt-2 max-w-md text-sm text-gray-400">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Badge({ children, tone = "default" }) {
  const tones = {
    default: "bg-brand-panel2 text-brand-gold border-brand-border",
    success: "bg-green-500/10 text-green-400 border-green-500/30",
    warning: "bg-brand-gold/10 text-brand-goldLight border-brand-gold/30",
    danger: "bg-red-500/10 text-red-400 border-red-500/30",
    gold: "bg-brand-gold/10 text-brand-gold border-brand-gold/30",
  };
  return (
    <span className={`component-badge text-xs px-2.5 py-1 rounded-full border font-medium ${tones[tone]}`}>{children}</span>
  );
}

export function ErrorText({ children }) {
  if (!children) return null;
  return <p className="text-red-400 text-sm mt-1">{children}</p>;
}

export function Input(props) {
  return (
    <input
      {...props}
      className={`component-input w-full px-4 py-2.5 rounded-lg bg-brand-panel border border-brand-border text-white placeholder-gray-500 focus:outline-none focus:border-brand-goldLight focus:ring-1 focus:ring-brand-goldLight ${props.className || ""}`}
    />
  );
}
