import { UserPlus, Search, MessageSquare, CreditCard, CheckCircle2 } from "lucide-react";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import Footer from "../../components/common/Footer";
import { SectionReveal } from "../../components/common/VisualEffects";

const steps = [
  {
    icon: UserPlus,
    title: "Sign Up",
    desc: "Register as a client or as an editor. Editors build a profile with skills, portfolio, and hourly rate; clients set up their account in seconds.",
  },
  {
    icon: Search,
    title: "Discover & Request",
    desc: "Clients browse verified editors by category — Image, TikTok, or Video — and send a project request describing what they need.",
  },
  {
    icon: MessageSquare,
    title: "Chat & Collaborate",
    desc: "Once an editor accepts, a real-time chat opens up. Share briefs, reference files, images, videos, and documents directly in the conversation.",
  },
  {
    icon: CreditCard,
    title: "Payment Protection",
    desc: "PayHere authorizes the project amount without capturing it. Card details remain on PayHere and the authorization stays on hold until client approval.",
  },
  {
    icon: CheckCircle2,
    title: "Delivery & Release",
    desc: "The editor uploads the final work. The client reviews it and explicitly approves capture; the platform fee and editor earning are recorded separately.",
  },
];

export default function AboutPage() {
  return (
    <div className="about-page min-h-screen bg-brand-dark">
      <LandingNavbar />
      <section className="marketing-page-enter mx-auto max-w-4xl px-5 py-16 sm:px-6 sm:py-20">
        <header className="about-heading mx-auto mb-16 max-w-2xl text-center">
          <p className="about-eyebrow">How it works</p>
          <h1 className="about-title font-display text-4xl font-bold sm:text-5xl">How EditZone Works</h1>
          <p className="about-subtitle mx-auto mt-5 max-w-2xl">
          A secure, end-to-end marketplace connecting clients with professional video editors —
          from discovery to delivery to payment.
          </p>
          <span className="about-divider" aria-hidden="true" />
        </header>

        <SectionReveal className="about-steps marketing-card-list space-y-10">
          {steps.map(({ icon: Icon, title, desc }, i) => (
            <div key={title} className="about-step-card glass flex gap-5 p-6 sm:gap-6 sm:p-7">
              <div className="about-step-icon flex h-12 w-12 shrink-0 items-center justify-center rounded-full">
                <Icon size={22} aria-hidden="true" />
              </div>
              <div>
                <p className="about-step-label mb-1">Step {i + 1}</p>
                <h3 className="mb-1 text-lg font-semibold text-white">{title}</h3>
                <p className="about-step-copy text-sm leading-relaxed">{desc}</p>
              </div>
            </div>
          ))}
        </SectionReveal>

        <SectionReveal delay={80} className="about-closing-card marketing-closing-card glass mt-16 p-8">
          <h2 className="font-display text-xl font-bold mb-3">Secure Communication & Payments</h2>
          <p className="text-gray-400 text-sm leading-relaxed">
            All messaging happens on-platform through encrypted, real-time chat — no need to share
            personal contact details. PayHere Sandbox authorizes the project payment first and EditZone
            captures it only after the authenticated project owner approves the delivered work. This is
            Hold Until Approval payment protection, not a legally regulated escrow service.
          </p>
        </SectionReveal>
      </section>
      <Footer />
    </div>
  );
}
