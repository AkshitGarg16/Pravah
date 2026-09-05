/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // Light theme only. Using 'class' means `dark:` variants only activate with an
  // explicit `dark` class, which is never added — effectively disabling dark mode
  // without opting into the OS `prefers-color-scheme` behaviour of `media`.
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        navy: '#0B1F3A',
        deep: '#0E2E52',
        teal: '#18A0A8',
        mint: '#3FC7B4',
        healthy: '#2FA98C',
        amber: '#E8A33D',
        coral: '#E4674F',
        purple: '#6B3FA0',
        surface: '#F8FAFB',
        card: '#FFFFFF',
        edge: '#E4EAF0',
        ink: '#16222E',
        muted: '#6B7D8E',
        faint: '#9AABB8',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
