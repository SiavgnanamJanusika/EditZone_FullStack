import { ArrowLeft, Clapperboard, Code2, Heart, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import Footer from "../../components/common/Footer";
import LandingNavbar from "../../components/navbar/LandingNavbar";

export default function CreditsPage() {
  return (
    <div className="credits-page min-h-screen overflow-hidden bg-brand-dark">
      <LandingNavbar />
      <main className="relative mx-auto grid min-h-[70vh] max-w-5xl place-items-center px-6 py-20 text-center">
        <div className="credits-orb credits-orb-one" />
        <div className="credits-orb credits-orb-two" />
        <section className="liquid-glass credits-card relative z-10 w-full max-w-3xl rounded-[2.25rem] px-6 py-14 sm:px-12">
          <div className="credits-icon mx-auto mb-7 grid h-20 w-20 place-items-center rounded-[1.6rem]">
            <Sparkles size={34} />
          </div>
          <p className="mb-3 text-xs font-bold uppercase tracking-[0.34em] text-brand-goldLight">The people behind the pixels</p>
          <h1 className="font-display text-4xl font-black text-white sm:text-6xl">Full Credits</h1>
          <p className="mx-auto mt-6 max-w-xl text-base leading-7 text-slate-300">
            Designed and crafted with care by <span className="font-semibold text-white">Janusika </span> for the editors, creators, and stories that make every frame matter.
          </p>
          <div className="mt-10 grid gap-3 sm:grid-cols-3">
            {[{ icon: Code2, label: "Product & Build" }, { icon: Clapperboard, label: "Creative Direction" }, { icon: Heart, label: "Made with care" }].map(({ icon: Icon, label }, index) => (
              <div key={label} className="credit-chip" style={{ animationDelay: `${index * 140 + 250}ms` }}>
                <Icon size={18} /><span>{label}</span>
              </div>
            ))}
          </div>
          <p className="mt-10 text-xs uppercase tracking-[0.24em] text-slate-500">© August 2026 UKI Kilinochchi</p>
          <Link to="/" className="mt-8 inline-flex items-center gap-2 text-sm font-semibold text-brand-goldLight hover:text-white"><ArrowLeft size={16} /> Back home</Link>
        </section>
      </main>
      <Footer />
    </div>
  );
}
