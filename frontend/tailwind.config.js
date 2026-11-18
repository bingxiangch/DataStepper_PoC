/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#F6F5FB",
        panel: "#FFFFFF",
        ink: "#1E1B2E",
        muted: "#6F6982",
        faint: "#9A94AB",
        line: "#E8E4F2",
        accent: {
          50: "#F3EFFF",
          100: "#E9E2FF",
          200: "#D6C8FF",
          300: "#BBA2FF",
          400: "#9B77FF",
          500: "#7C4DFF",
          600: "#6B3BE8",
          700: "#582DC2",
        },
        observed: "#17845B",
        preview: "#9A5B00",
        danger: "#B42318",
      },
      boxShadow: {
        card: "0 1px 2px rgba(30, 27, 46, 0.04), 0 8px 24px rgba(30, 27, 46, 0.04)",
        float: "0 18px 50px rgba(30, 27, 46, 0.16)",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["SFMono-Regular", "Menlo", "Consolas", "Liberation Mono", "monospace"],
      },
    },
  },
  plugins: [],
};
