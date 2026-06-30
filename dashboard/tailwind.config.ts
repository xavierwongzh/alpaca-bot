import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dark theme palette
        ink: {
          950: "#0a0b0f",
          900: "#0f1117",
          850: "#151823",
          800: "#1b1f2a",
          700: "#252a38",
          600: "#323848",
        },
        profit: "#22c55e",
        loss: "#ef4444",
        accent: "#6366f1",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
