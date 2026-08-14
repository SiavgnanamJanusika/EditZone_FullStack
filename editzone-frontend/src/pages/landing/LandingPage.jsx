import { useNavigate } from "react-router-dom";
import { ShieldCheck, Zap, Wallet, Star, Camera, Play, Share2, ArrowUpRight } from "lucide-react";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import { OrbitLogo, PrimaryButton, OutlineButton } from "../../components/common/UI";
import Footer from "../../components/common/Footer";
import { SectionReveal } from "../../components/common/VisualEffects";

export default function LandingPage() {
  const navigate = useNavigate();

  return (
    <div className="landing-page min-h-screen">
      <LandingNavbar />

      <main>
      <section className="mx-auto max-w-[1440px] px-4 pb-8 pt-5 sm:px-8 sm:pb-12 sm:pt-8">
        <div className="editorial-hero">
          <div className="editorial-hero-shade" />
          <div className="editorial-art" aria-hidden="true">
            <div className="editorial-art-glow" />
            <div className="editorial-screen">
              <div className="editorial-screen-bar"><span /><span /><span /></div>
              <div className="editorial-preview"><OrbitLogo size={259} logoSize={161} className="landing-orbit" /></div>
              <div className="editorial-timeline">
                <span className="track track-one" /><span className="track track-two" /><span className="track track-three" />
                <i />
              </div>
            </div>
          </div>
          <div className="relative z-10 flex h-full min-h-[620px] items-end px-7 py-12 sm:px-12 lg:min-h-[700px] lg:px-16 lg:py-16">
            <div className="editorial-copy max-w-3xl pb-5">
              <span className="landing-badge mb-5 inline-flex items-center gap-2 text-[.68rem] font-bold uppercase tracking-[.2em]"><span aria-hidden="true">✦</span> Professional editing, simplified</span>
              <h1 className="editorial-title text-white">
                <span className="editorial-title-line">Hire Professional</span>
                <span className="editorial-title-accent">Video Editors</span>
                <span className="editorial-title-line editorial-title-end">in Minutes</span>
              </h1>
              <p className="landing-description mt-7 max-w-xl text-sm leading-7 sm:text-base">
                EditZone connects you with verified, skilled video editors for TikTok, YouTube, and every format in between — with <strong>Hold Until Approval</strong> payment protection and real-time collaboration built in.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <PrimaryButton onClick={() => navigate("/choose-role")} className="landing-silver-button px-7 py-3">Get Started <ArrowUpRight size={17} aria-hidden="true" /></PrimaryButton>
                <OutlineButton onClick={() => navigate("/why-us")} className="landing-outline-button px-7 py-3"><span className="landing-button-icon"><Play size={10} fill="currentColor" aria-hidden="true" /></span>Why EditZone?</OutlineButton>
              </div>
            </div>
          </div>
          <aside className="editorial-social-rail" aria-label="Social links">
            <span className="editorial-rail-line" />
            <a href="#features" aria-label="Creator gallery"><Camera size={17} /></a>
            <a href="#features" aria-label="Video showcase"><Play size={18} fill="currentColor" /></a>
            <a href="#features" aria-label="Share"><Share2 size={17} /></a>
            <span className="editorial-rail-line" />
          </aside>
          <div className="editorial-page-mark" aria-hidden="true"><span className="active" /><span /><span /></div>
        </div>
      </section>

      <SectionReveal as="section" id="features" className="feature-grid max-w-6xl mx-auto px-6 pb-24 pt-10 grid grid-cols-1 md:grid-cols-4 gap-6">
        {[
          { icon: ShieldCheck, title: "Verified Editors", desc: "Every editor is vetted for skill and reliability." },
          { icon: Wallet, title: "Payment Protection", desc: "PayHere capture waits until you approve the delivered work." },
          { icon: Zap, title: "Fast Delivery", desc: "Get professionally edited videos, fast." },
          { icon: Star, title: "Rated & Reviewed", desc: "Transparent ratings from real clients." },
        ].map(({ icon: Icon, title, desc }) => (
          <div key={title} className="glass cinematic-card rounded-2xl p-7 min-h-[190px]">
            <span className="feature-icon"><Icon size={25} aria-hidden="true" /></span>
            <h3 className="font-semibold text-white mb-1">{title}</h3>
            <p className="text-sm text-gray-400">{desc}</p>
          </div>
        ))}
      </SectionReveal>

      <SectionReveal as="section" delay={80} className="max-w-4xl mx-auto px-6 pb-24 text-center">
        <div className="glass cinematic-card cta-card rounded-2xl p-10">
          <h2 className="font-display text-2xl font-bold mb-3">Ready to bring your videos to life?</h2>
          <p className="text-gray-400 mb-6">Join EditZone today as a client or as a professional editor.</p>
          <PrimaryButton onClick={() => navigate("/choose-role")} className="landing-silver-button">Register Now <ArrowUpRight size={16} aria-hidden="true" /></PrimaryButton>
        </div>
      </SectionReveal>
      </main>
      <Footer />
    </div>
  );
}
