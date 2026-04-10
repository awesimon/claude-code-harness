/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      /* ==================== Colors ==================== */
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",

        /* Brand colors */
        brand: {
          DEFAULT: "hsl(var(--brand-primary))",
          hover: "hsl(var(--brand-primary-hover))",
          active: "hsl(var(--brand-primary-active))",
          muted: "hsl(var(--brand-primary-muted))",
          secondary: "hsl(var(--brand-secondary))",
        },

        /* Surface colors */
        surface: {
          DEFAULT: "hsl(var(--surface))",
          hover: "hsl(var(--surface-hover))",
          active: "hsl(var(--surface-active))",
        },

        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },

      /* ==================== Border Radius ==================== */
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "calc(var(--radius) + 4px)",
        "2xl": "calc(var(--radius) + 8px)",
      },

      /* ==================== Typography ==================== */
      fontFamily: {
        sans: ['Geist', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['Monaco', 'Menlo', 'Consolas', 'monospace'],
        display: ['Geist', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }],
        '3xs': ['0.5rem', { lineHeight: '0.75rem' }],
      },
      lineHeight: {
        'tighter': '1.1',
        'snug': '1.375',
      },
      letterSpacing: {
        'tighter': '-0.04em',
        'tight': '-0.02em',
        'wide': '0.02em',
        'wider': '0.04em',
        'widest': '0.08em',
      },

      /* ==================== Spacing ==================== */
      spacing: {
        '18': '4.5rem',
        '22': '5.5rem',
        '30': '7.5rem',
        '34': '8.5rem',
        '38': '9.5rem',
        '88': '22rem',
        '128': '32rem',
      },

      /* ==================== Animation ==================== */
      animation: {
        /* Accordion */
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",

        /* Fade animations */
        "fade-in": "fadeIn 0.3s ease-out",
        "fade-out": "fadeOut 0.3s ease-in",
        "fade-in-up": "fadeInUp 0.3s ease-out",
        "fade-in-down": "fadeInDown 0.3s ease-out",

        /* Slide animations */
        "slide-in-left": "slideInLeft 0.3s ease-out",
        "slide-in-right": "slideInRight 0.3s ease-out",
        "slide-in-up": "slideInUp 0.3s ease-out",
        "slide-in-down": "slideInDown 0.3s ease-out",

        /* Scale animations */
        "scale-in": "scaleIn 0.3s ease-out",
        "scale-out": "scaleOut 0.15s ease-in",

        /* Special effects */
        "shimmer": "shimmer 1.5s infinite",
        "pulse": "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "spin": "spin 1s linear infinite",
        "spin-slow": "spin 3s linear infinite",
        "bounce": "bounce 1s infinite",

        /* Delayed entrance */
        "fade-in-delay-1": "fadeIn 0.3s ease-out 0.1s backwards",
        "fade-in-delay-2": "fadeIn 0.3s ease-out 0.2s backwards",
        "fade-in-delay-3": "fadeIn 0.3s ease-out 0.3s backwards",
      },

      keyframes: {
        /* Accordion */
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },

        /* Fade */
        fadeIn: {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        fadeOut: {
          from: { opacity: "1" },
          to: { opacity: "0" },
        },
        fadeInUp: {
          from: { opacity: "0", transform: "translateY(20px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        fadeInDown: {
          from: { opacity: "0", transform: "translateY(-20px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },

        /* Slide */
        slideInLeft: {
          from: { opacity: "0", transform: "translateX(-30px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        slideInRight: {
          from: { opacity: "0", transform: "translateX(30px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        slideInUp: {
          from: { opacity: "0", transform: "translateY(30px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        slideInDown: {
          from: { opacity: "0", transform: "translateY(-30px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },

        /* Scale */
        scaleIn: {
          from: { opacity: "0", transform: "scale(0.95)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        scaleOut: {
          from: { opacity: "1", transform: "scale(1)" },
          to: { opacity: "0", transform: "scale(0.95)" },
        },

        /* Shimmer */
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },

        /* Pulse */
        pulse: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.5" },
        },

        /* Spin */
        spin: {
          from: { transform: "rotate(0deg)" },
          to: { transform: "rotate(360deg)" },
        },

        /* Bounce */
        bounce: {
          "0%, 100%": {
            transform: "translateY(0)",
            animationTimingFunction: "cubic-bezier(0.8, 0, 1, 1)",
          },
          "50%": {
            transform: "translateY(-25%)",
            animationTimingFunction: "cubic-bezier(0, 0, 0.2, 1)",
          },
        },
      },

      /* ==================== Transitions ==================== */
      transitionDuration: {
        '50': '50ms',
        '400': '400ms',
        '600': '600ms',
        '800': '800ms',
        '1000': '1000ms',
      },
      transitionTimingFunction: {
        'in-expo': 'cubic-bezier(0.16, 1, 0.3, 1)',
        'out-expo': 'cubic-bezier(0.7, 0, 0.84, 0)',
        'in-out-expo': 'cubic-bezier(0.87, 0, 0.13, 1)',
        'spring': 'cubic-bezier(0.34, 1.56, 0.64, 1)',
      },

      /* ==================== Box Shadow ==================== */
      boxShadow: {
        'glass': '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06), inset 0 1px 0 0 rgba(255, 255, 255, 0.05)',
        'glass-strong': '0 10px 15px -3px rgba(0, 0, 0, 0.15), 0 4px 6px -2px rgba(0, 0, 0, 0.1), inset 0 1px 0 0 rgba(255, 255, 255, 0.1)',
        'glow': '0 0 20px hsl(var(--primary) / 0.3)',
        'glow-strong': '0 0 30px hsl(var(--primary) / 0.4)',
        'inner-light': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.05)',
        'inner-light-strong': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.1)',
      },

      /* ==================== Backdrop Blur ==================== */
      backdropBlur: {
        'xs': '2px',
      },

      /* ==================== Z-Index ==================== */
      zIndex: {
        'dropdown': '100',
        'sticky': '200',
        'fixed': '300',
        'modal-backdrop': '400',
        'modal': '500',
        'popover': '600',
        'tooltip': '700',
        'toast': '800',
      },

      /* ==================== Opacity ==================== */
      opacity: {
        '2': '0.02',
        '3': '0.03',
        '4': '0.04',
        '6': '0.06',
        '8': '0.08',
        '12': '0.12',
        '15': '0.15',
        '85': '0.85',
        '98': '0.98',
      },

      /* ==================== Screens ==================== */
      screens: {
        'xs': '480px',
        '3xl': '1920px',
      },
    },
  },
  plugins: [
    require("tailwindcss-animate"),
    require("@tailwindcss/typography"),

    /* Custom plugin for additional utilities */
    function({ addUtilities, theme }) {
      addUtilities({
        /* Text utilities */
        '.text-gradient': {
          background: `linear-gradient(135deg, hsl(var(--primary)), hsl(var(--accent)))`,
          '-webkit-background-clip': 'text',
          'background-clip': 'text',
          '-webkit-text-fill-color': 'transparent',
        },

        /* Animation utilities */
        '.animation-delay-100': {
          'animation-delay': '100ms',
        },
        '.animation-delay-200': {
          'animation-delay': '200ms',
        },
        '.animation-delay-300': {
          'animation-delay': '300ms',
        },
        '.animation-delay-400': {
          'animation-delay': '400ms',
        },
        '.animation-delay-500': {
          'animation-delay': '500ms',
        },

        /* Line clamp utilities */
        '.line-clamp-1': {
          overflow: 'hidden',
          display: '-webkit-box',
          '-webkit-box-orient': 'vertical',
          '-webkit-line-clamp': '1',
        },
        '.line-clamp-2': {
          overflow: 'hidden',
          display: '-webkit-box',
          '-webkit-box-orient': 'vertical',
          '-webkit-line-clamp': '2',
        },
        '.line-clamp-3': {
          overflow: 'hidden',
          display: '-webkit-box',
          '-webkit-box-orient': 'vertical',
          '-webkit-line-clamp': '3',
        },

        /* Safe area utilities for mobile */
        '.pb-safe': {
          'padding-bottom': 'env(safe-area-inset-bottom)',
        },
        '.pt-safe': {
          'padding-top': 'env(safe-area-inset-top)',
        },
        '.pl-safe': {
          'padding-left': 'env(safe-area-inset-left)',
        },
        '.pr-safe': {
          'padding-right': 'env(safe-area-inset-right)',
        },
      });
    },
  ],
}
