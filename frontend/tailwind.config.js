// frontend/tailwind.config.js
//
// Colors and font resolve to the theme.css custom properties so the two
// never drift; theme.css is the source of truth for the token values.
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "var(--paper)",
        ink: "var(--ink)",
        rule: "var(--rule)",
        accent: "var(--accent)",
        ok: "var(--ok)",
        flag: "var(--flag)",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
      },
      maxWidth: {
        measure: "68ch",
      },
    },
  },
  plugins: [],
};
