import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: '#10b981', dark: '#059669' },
        surface: { DEFAULT: '#1e1e2e', muted: '#2a2a3e' },
      },
    },
  },
  plugins: [],
};

export default config;
