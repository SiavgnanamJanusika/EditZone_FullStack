import { ArrowLeft, Home, Map } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { Logo } from "../components/common/UI";

export default function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <main className="grid min-h-screen place-items-center bg-brand-dark px-6 text-center text-white">
      <section className="glass motion-panel w-full max-w-xl rounded-3xl p-8 sm:p-12">
        <Logo size={64} />
        <div className="mx-auto mt-7 grid h-20 w-20 place-items-center rounded-3xl bg-brand-gold/10 text-brand-gold"><Map size={38} /></div>
        <p className="mt-6 text-sm font-bold uppercase tracking-[.24em] text-brand-gold">Error 404</p>
        <h1 className="mt-2 font-display text-3xl font-bold">Page not found</h1>
        <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-gray-400">The page may have moved or the address may be incorrect.</p>
        <div className="mt-7 flex flex-col justify-center gap-3 sm:flex-row">
          <button type="button" onClick={() => navigate(-1)} className="btn-secondary"><ArrowLeft size={17} /> Go back</button>
          <Link to="/" className="btn-primary"><Home size={17} /> Home page</Link>
        </div>
      </section>
    </main>
  );
}
