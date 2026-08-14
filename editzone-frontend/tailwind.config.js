/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          gold: "#D4AF37",
          goldDeep: "#9C7518",
          goldLight: "#F2D06B",
          goldWarm: "#E5B94B",
          rating: "#F4C542",
          dark: "#050505",
          panel: "#111111",
          panel2: "#151515",
          border: "rgba(255,255,255,0.08)",
          goldBorder: "rgba(212,175,55,0.22)",
        },
      },
      fontFamily: {
        display: ["Poppins", "sans-serif"],
        body: ["Inter", "sans-serif"],
        sans: ["Inter", "sans-serif"],
      },
      backgroundImage: {
        "brand-gradient": "linear-gradient(135deg, #F2D06B 0%, #D4AF37 48%, #A47D1E 100%)",
        "avatar-gradient": "linear-gradient(135deg, #272727 0%, #111111 100%)",
        "brand-radial": "radial-gradient(circle at 50% 0%, rgba(212,175,55,0.18), transparent 60%)",
      },
      boxShadow: {
        glow: "0 0 25px rgba(212,175,55,0.16)",
        "glow-gold": "0 0 25px rgba(212,175,55,0.14)",
      },
    },
  },
  plugins: [],
};
