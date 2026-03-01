/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#217346",
          light: "#33a867",
          dark: "#1a5c38",
        },
        em: {
          bg: "#f5f5f7",
          card: "#ffffff",
          border: "#e5e7eb",
          "border-2": "#d1d5db",
          red: "#d13438",
          "red-light": "#e74c3c",
          gold: "#e5a100",
          cyan: "#0078d4",
          t1: "#1a1a1a",
          t2: "#4b5563",
          t3: "#9ca3af",
          t4: "#d1d5db",
        },
      },
      borderRadius: {
        em: "12px",
        "em-sm": "8px",
      },
      animation: {
        "logo-breathe": "logo-breathe 3s ease-in-out infinite",
        "logo-shine": "logo-shine 4s ease-in-out infinite",
        "orb-float": "orb-float 8s ease-in-out infinite",
        "success-pop": "success-pop 0.6s cubic-bezier(0.175, 0.885, 0.32, 1.275)",
        "fade-up": "fade-up 0.5s ease-out forwards",
        "fade-up-delayed": "fade-up 0.5s ease-out 0.15s forwards",
        "panel-in": "panel-in 0.4s cubic-bezier(0.22, 1, 0.36, 1)",
        shimmer: "shimmer 1.8s infinite",
        "pulse-ring": "pulse-ring 2s ease-out infinite",
        "check-draw": "check-draw 0.4s ease-out 0.1s forwards",
        "confetti-1": "confetti 1.2s ease-out forwards",
        "confetti-2": "confetti 1.4s ease-out 0.1s forwards",
        "confetti-3": "confetti 1.1s ease-out 0.2s forwards",
        "progress-glow": "progress-glow 2s ease-in-out infinite",
        "slide-in": "slide-in 0.35s cubic-bezier(0.22, 1, 0.36, 1) forwards",
      },
      keyframes: {
        "logo-breathe": {
          "0%, 100%": { transform: "scale(1)", opacity: "1" },
          "50%": { transform: "scale(1.04)", opacity: "0.92" },
        },
        "logo-shine": {
          "0%, 100%": { backgroundPosition: "-200% center" },
          "50%": { backgroundPosition: "200% center" },
        },
        "orb-float": {
          "0%, 100%": { transform: "translateY(0) scale(1)" },
          "50%": { transform: "translateY(-20px) scale(1.05)" },
        },
        "success-pop": {
          "0%": { transform: "scale(0) rotate(-10deg)", opacity: "0" },
          "50%": { transform: "scale(1.25) rotate(5deg)" },
          "100%": { transform: "scale(1) rotate(0deg)", opacity: "1" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "panel-in": {
          from: { opacity: "0", transform: "translateY(16px) scale(0.98)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        shimmer: {
          from: { transform: "translateX(-60px)" },
          to: { transform: "translateX(400px)" },
        },
        "pulse-ring": {
          "0%": { transform: "scale(1)", opacity: "0.6" },
          "100%": { transform: "scale(1.8)", opacity: "0" },
        },
        "check-draw": {
          "0%": { strokeDashoffset: "24" },
          "100%": { strokeDashoffset: "0" },
        },
        confetti: {
          "0%": { transform: "translateY(0) rotate(0deg) scale(1)", opacity: "1" },
          "100%": { transform: "translateY(-80px) rotate(720deg) scale(0)", opacity: "0" },
        },
        "progress-glow": {
          "0%, 100%": { boxShadow: "0 0 8px rgba(33,115,70,.2)" },
          "50%": { boxShadow: "0 0 20px rgba(33,115,70,.4)" },
        },
        "slide-in": {
          from: { opacity: "0", transform: "translateX(-8px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
      },
    },
  },
  plugins: [],
};
