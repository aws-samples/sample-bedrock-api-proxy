/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "primary": "#2b6cee",
        "background-light": "#f6f6f8",
        "background-dark": "#101622",
        "surface-dark": "#1a202c",
        "border-dark": "#2d3748",
        "input-border": "#282e39",
        "input-bg": "#161b26",
      },
      fontFamily: {
        "display": ["Space Grotesk", "sans-serif"],
        "sans": ["Space Grotesk", "sans-serif"],
      },
      borderRadius: {
        "DEFAULT": "0.25rem",
        "lg": "0.5rem",
        "xl": "0.75rem",
        "2xl": "1rem",
        "full": "9999px"
      },
    },
  },
  plugins: [],
}
