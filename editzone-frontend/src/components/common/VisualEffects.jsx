import { useEffect, useRef, useState } from "react";

export function CinematicBackdrop() {
  return (
    <div className="cinematic-backdrop" aria-hidden="true">
      <span className="cinematic-orb cinematic-orb-one" />
      <span className="cinematic-orb cinematic-orb-two" />
      <span className="cinematic-beam" />
      <span className="cinematic-grid" />
      <span className="cinematic-grain" />
    </div>
  );
}

export function SectionReveal({ children, className = "", direction = "up", delay = 0, as: Element = "div", ...props }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    if (!("IntersectionObserver" in window)) {
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -8%", threshold: 0.08 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <Element
      {...props}
      ref={ref}
      className={`section-reveal section-reveal-${direction} ${visible ? "is-visible" : ""} ${className}`}
      style={{ "--reveal-delay": `${delay}ms` }}
    >
      {children}
    </Element>
  );
}
