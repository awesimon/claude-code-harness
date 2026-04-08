import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { motion, useReducedMotion, type Variants } from 'framer-motion';
import { cn } from '@/lib/utils';
import { CircleNotch } from '@phosphor-icons/react';

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  asChild?: boolean;
  variant?: 'default' | 'secondary' | 'outline' | 'ghost' | 'link' | 'destructive' | 'glass';
  size?: 'default' | 'sm' | 'lg' | 'icon';
  loading?: boolean;
  magnetic?: boolean;
}

/**
 * Button Component
 *
 * A versatile button component with Glass morphism support,
 * magnetic interaction effects, and smooth animations.
 *
 * @example
 * ```tsx
 * <Button variant="glass" size="lg" magnetic>
 *   Click me
 * </Button>
 * ```
 */
const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = 'default',
      size = 'default',
      asChild = false,
      loading = false,
      magnetic = false,
      disabled,
      children,
      ...props
    },
    ref
  ) => {
    const Comp = asChild ? Slot : 'button';
    const shouldReduceMotion = useReducedMotion();
    const [magneticOffset, setMagneticOffset] = React.useState({ x: 0, y: 0 });
    const buttonRef = React.useRef<HTMLButtonElement>(null);

    // Combine forwarded ref with local ref
    React.useImperativeHandle(ref, () => buttonRef.current as HTMLButtonElement);

    /**
     * Magnetic effect handler
     * Creates a subtle pull effect when cursor is near the button
     */
    const handleMouseMove = React.useCallback(
      (e: React.MouseEvent<HTMLButtonElement>) => {
        if (!magnetic || shouldReduceMotion) return;

        const button = buttonRef.current;
        if (!button) return;

        const rect = button.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;

        const distanceX = e.clientX - centerX;
        const distanceY = e.clientY - centerY;

        // Magnetic pull strength (max 8px displacement)
        const strength = 0.15;
        setMagneticOffset({
          x: distanceX * strength,
          y: distanceY * strength,
        });
      },
      [magnetic, shouldReduceMotion]
    );

    const handleMouseLeave = React.useCallback(() => {
      setMagneticOffset({ x: 0, y: 0 });
    }, []);

    // Loading spinner animation
    const spinnerVariants: Variants = {
      animate: {
        rotate: 360,
        transition: {
          duration: 1,
          repeat: Infinity,
          ease: 'linear',
        },
      },
    };

    // Button scale animation for tap feedback
    const tapVariants: Variants = {
      tap: { scale: 0.97 },
      hover: { scale: 1.02 },
    };

    const isDisabled = disabled || loading;

    const buttonContent = (
      <>
        {loading && (
          <motion.span
            className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
            variants={shouldReduceMotion ? undefined : spinnerVariants}
            animate="animate"
          >
            <CircleNotch
              className={cn(
                'animate-spin',
                size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4',
                variant === 'default' || variant === 'destructive'
                  ? 'text-current opacity-70'
                  : 'text-primary'
              )}
              weight="bold"
            />
          </motion.span>
        )}
        <span
          className={cn(
            'flex items-center justify-center gap-2',
            loading && 'opacity-0'
          )}
        >
          {children}
        </span>
      </>
    );

    return (
      <motion.div
        className="inline-block"
        animate={{
          x: magneticOffset.x,
          y: magneticOffset.y,
        }}
        transition={{
          type: 'spring',
          stiffness: 350,
          damping: 15,
          mass: 0.5,
        }}
      >
        <Comp
          className={cn(
            // Base styles
            'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl text-sm font-medium',
            'relative overflow-hidden',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
            'disabled:pointer-events-none disabled:opacity-50',
            // Transition
            'transition-all duration-200 ease-out',
            // Variant styles
            {
              // Default - Primary accent with shadow
              'bg-primary text-primary-foreground shadow-lg shadow-primary/25 hover:bg-primary/90 hover:shadow-primary/40':
                variant === 'default',
              // Secondary - Muted
              'bg-secondary text-secondary-foreground hover:bg-secondary/80':
                variant === 'secondary',
              // Outline - Subtle border
              'border border-white/20 bg-transparent hover:bg-white/5 hover:border-white/30':
                variant === 'outline',
              // Ghost - Transparent with hover
              'hover:bg-white/5': variant === 'ghost',
              // Link - Text only
              'text-primary underline-offset-4 hover:underline':
                variant === 'link',
              // Destructive
              'bg-destructive text-destructive-foreground shadow-lg shadow-destructive/25 hover:bg-destructive/90':
                variant === 'destructive',
              // Glass - Premium glass morphism
              'glass text-foreground hover:bg-white/[0.06] hover:border-white/20 hover:shadow-lg hover:shadow-black/5':
                variant === 'glass',
            },
            // Size styles
            {
              'h-10 px-4 py-2': size === 'default',
              'h-8 rounded-lg px-3 text-xs': size === 'sm',
              'h-11 rounded-xl px-8': size === 'lg',
              'h-9 w-9 p-0 rounded-xl': size === 'icon',
            },
            className
          )}
          ref={buttonRef}
          disabled={isDisabled}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          {...props}
        >
          {shouldReduceMotion ? (
            buttonContent
          ) : (
            <motion.span
              className="flex w-full items-center justify-center gap-2"
              variants={tapVariants}
              whileTap="tap"
              whileHover={magnetic ? undefined : 'hover'}
            >
              {buttonContent}
            </motion.span>
          )}
        </Comp>
      </motion.div>
    );
  }
);
Button.displayName = 'Button';

export { Button };
