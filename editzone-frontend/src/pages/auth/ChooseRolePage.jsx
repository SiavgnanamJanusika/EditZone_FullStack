import { useNavigate } from "react-router-dom";
import { User, Clapperboard } from "lucide-react";
import LandingNavbar from "../../components/navbar/LandingNavbar";
import { Logo } from "../../components/common/UI";

export default function ChooseRolePage() {
  const navigate = useNavigate();

  return (
    <div className="choose-role-page min-h-screen bg-brand-dark">
      <LandingNavbar />
      <section className="choose-role-shell mx-auto px-5 py-14 text-center sm:px-6 sm:py-20">
        <div className="choose-role-logo mb-7 flex justify-center"><Logo size={78} /></div>
        <p className="choose-role-eyebrow">Choose your role</p>
        <h1 className="choose-role-title font-display text-3xl font-bold sm:text-4xl">Join EditZone as...</h1>
        <p className="choose-role-subtitle mx-auto mt-4 max-w-xl">Choose the role that fits you. You can only pick one per account.</p>
        <span className="choose-role-divider" aria-hidden="true" />

        <div className="choose-role-grid grid grid-cols-1 gap-7 sm:grid-cols-2">
          <button
            onClick={() => navigate("/register?role=user")}
            className="choose-role-card glass group min-h-[270px] p-8 text-left sm:p-9"
          >
            <div className="choose-role-icon mb-6 flex h-14 w-14 items-center justify-center">
              <User size={26} aria-hidden="true" />
            </div>
            <h3 className="choose-role-card-title mb-3 font-display text-xl font-bold">I'm a Client</h3>
            <p className="choose-role-card-copy text-sm">
              I want to hire professional editors for my videos, TikToks, or content projects.
            </p>
          </button>

          <button
            onClick={() => navigate("/register?role=editor")}
            className="choose-role-card glass group min-h-[270px] p-8 text-left sm:p-9"
          >
            <div className="choose-role-icon mb-6 flex h-14 w-14 items-center justify-center">
              <Clapperboard size={26} aria-hidden="true" />
            </div>
            <h3 className="choose-role-card-title mb-3 font-display text-xl font-bold">I'm an Editor</h3>
            <p className="choose-role-card-copy text-sm">
              I want to offer my video editing skills and get hired for paid projects.
            </p>
          </button>
        </div>

        <p className="choose-role-login-copy mt-11 text-sm">
          Already have an account?{" "}
          <button onClick={() => navigate("/login")} className="choose-role-login-link">
            Log in
          </button>
        </p>
      </section>
    </div>
  );
}
