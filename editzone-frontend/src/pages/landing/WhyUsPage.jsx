import { ShieldCheck, Lock, Sparkles, DollarSign, Rocket, Headphones } from "lucide-react";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import Footer from "../../components/common/Footer";
import { SectionReveal } from "../../components/common/VisualEffects";

const reasons = [
  { icon: ShieldCheck, title: "Verified Editors", desc: "Every editor on EditZone is reviewed before joining, so you know exactly who you're hiring." },
  { icon: Lock, title: "Hold Until Approval", desc: "PayHere authorizes first and capture happens only after the project owner approves delivered work." },
  { icon: Sparkles, title: "High-Quality Editing", desc: "From TikTok cuts to full YouTube productions, our editors bring professional craft to every project." },
  { icon: DollarSign, title: "Affordable Pricing", desc: "Transparent hourly rates set by editors, with no hidden fees — you always know what you're paying for." },
  { icon: Rocket, title: "Fast Delivery", desc: "Real-time chat and streamlined workflows mean projects move quickly from brief to final cut." },
  { icon: Headphones, title: "Reliable Support", desc: "Our team monitors every delivery and is on hand to resolve disputes or issues quickly." },
];

export default function WhyUsPage() {
  return (
    <div className="why-us-page min-h-screen bg-brand-dark">
      <LandingNavbar />
      <section className="marketing-page-enter mx-auto max-w-6xl px-5 py-16 sm:px-6 sm:py-20">
        <header className="why-us-heading mx-auto mb-16 max-w-2xl text-center">
          <p className="why-us-eyebrow">Why EditZone?</p>
          <h1 className="why-us-title font-display text-4xl font-bold sm:text-5xl">Why Choose EditZone</h1>
          <p className="why-us-subtitle mx-auto mt-5 max-w-2xl">
            We built EditZone to make hiring a video editor as safe and simple as it should be.
          </p>
          <span className="why-us-divider" aria-hidden="true" />
        </header>

        <SectionReveal className="why-us-grid marketing-card-list grid grid-cols-1 gap-7 sm:grid-cols-2 lg:grid-cols-3">
          {reasons.map(({ icon: Icon, title, desc }) => (
            <div key={title} className="why-us-card glass flex min-h-[220px] flex-col p-7">
              <div className="why-us-icon mb-5 flex h-12 w-12 items-center justify-center rounded-xl">
                <Icon size={21} aria-hidden="true" />
              </div>
              <h3 className="why-us-card-title mb-2 font-semibold">{title}</h3>
              <p className="why-us-card-copy text-sm leading-relaxed">{desc}</p>
            </div>
          ))}
        </SectionReveal>
      </section>
      <Footer />
    </div>
  );
}
