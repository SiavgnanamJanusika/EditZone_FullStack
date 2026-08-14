import { Link, NavLink, useNavigate } from "react-router-dom";
import { Menu } from "lucide-react";
import { Logo, OutlineButton, PrimaryButton } from "../common/UI";
import { useAuth } from "../../context/AuthContext";
import useScrolledSurface from "../common/useScrolledSurface";

export default function LandingNavbar() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const scrolled = useScrolledSurface();

  return (
    <nav className={`landing-nav-shell sticky top-0 z-50 ${scrolled ? "is-scrolled" : ""}`} aria-label="Main navigation">
      <div className="landing-nav mx-auto flex max-w-[1380px] items-center justify-between gap-4 px-4 sm:px-6">
        <Link to="/" className="landing-brand shrink-0" aria-label="EditZone home">
          <Logo size={60} withText premium />
        </Link>
        <div className="landing-nav-links hidden items-center gap-7 text-sm font-medium md:flex">
          {[['/', 'Home'], ['/about', 'About'], ['/why-us', 'Why Us'], ['/credits', 'Credits']].map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `landing-nav-link ${isActive ? 'active' : ''}`}>{label}</NavLink>
          ))}
        </div>
        {user ? (
          <PrimaryButton onClick={() => navigate(user.role === "editor" ? "/editor/dashboard" : "/editors")} className="landing-silver-button">
            Dashboard
          </PrimaryButton>
        ) : (
          <div className="flex items-center gap-2">
            <OutlineButton onClick={() => navigate("/login")} className="landing-sign-in hidden px-5 sm:inline-flex">Sign In</OutlineButton>
            <PrimaryButton onClick={() => navigate("/choose-role")} className="landing-register px-5">Register</PrimaryButton>
            <details className="landing-mobile-menu relative md:hidden">
              <summary aria-label="Open navigation menu"><Menu size={20} /></summary>
              <div className="absolute right-0 top-[calc(100%+.75rem)] grid min-w-44 gap-1 p-2">
                {[['/', 'Home'], ['/about', 'About'], ['/why-us', 'Why Us'], ['/credits', 'Credits']].map(([to, label]) => <Link key={to} to={to}>{label}</Link>)}
                <Link className="sm:hidden" to="/login">Sign In</Link>
              </div>
            </details>
          </div>
        )}
      </div>
    </nav>
  );
}
