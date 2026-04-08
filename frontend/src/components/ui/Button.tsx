import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { motion, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils';

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  asChild?: boolean;
  variant?: 'default' | 'secondary' | 'outline' | 'ghost' | 'link' | 'destructive';
  size?: 'default' | 'sm' | 'lg' | 'icon';
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'default', asChild = false, children, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    const shouldReduceMotion = useReducedMotion();

    return (
      <Comp
        className={cn(
          // Base styles
          'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium',
          'transition-all duration-200',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          'disabled:pointer-events-none disabled:opacity-50',
          // Tactile feedback
          'tap-highlight active:scale-[0.98]',
          {
            // Default - Primary accent
            'bg-primary text-primary-foreground shadow-lg shadow-primary/25 hover:bg-primary/90 hover:shadow-primary/40': variant === 'default',
            // Secondary - Muted
            'bg-secondary text-secondary-foreground hover:bg-secondary/80': variant === 'secondary',
            // Outline - Glass effect
            'glass hover:bg-white/5': variant === 'outline',
            // Ghost - Transparent with hover
            'hover:bg-white/5': variant === 'ghost',
            // Link - Text only
            'text-primary underline-offset-4 hover:underline': variant === 'link',
            // Destructive
            'bg-destructive text-destructive-foreground hover:bg-destructive/90': variant === 'destructive',
          },
          {
            'h-10 px-4 py-2': size === 'default',
            'h-8 rounded-lg px-3 text-xs': size === 'sm',
            'h-11 rounded-lg px-8': size === 'lg',
            'h-9 w-9 p-0': size === 'icon',
          },
          className
        )}
        ref={ref}
        {...props}
      >
        {shouldReduceMotion ? (
          children
        ) : (
          <motion.span
            className="flex items-center justify-center gap-2"
            whileTap={{ scale: 0.98 }}
          >
            {children}
          </motion.span>
        )}
      </Comp>
    );
  }
);
Button.displayName = 'Button';

export { Button };
