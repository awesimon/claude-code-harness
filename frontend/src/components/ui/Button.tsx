import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cn } from '@/lib/utils';
import { CircleNotch } from '@phosphor-icons/react';

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  asChild?: boolean;
  variant?: 'default' | 'secondary' | 'outline' | 'ghost' | 'link' | 'destructive';
  size?: 'default' | 'sm' | 'lg' | 'icon';
  loading?: boolean;
}

/**
 * Button Component
 *
 * A minimal button component with clean flat design.
 *
 * @example
 * ```tsx
 * <Button variant="default" size="lg">
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
      disabled,
      children,
      ...props
    },
    ref
  ) => {
    const Comp = asChild ? Slot : 'button';
    const isDisabled = disabled || loading;

    return (
      <Comp
        className={cn(
          // Base styles
          'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium',
          'relative overflow-hidden',
          'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground focus-visible:ring-offset-1 focus-visible:ring-offset-background',
          'disabled:pointer-events-none disabled:opacity-50',
          // Transition
          'transition-colors duration-200',
          // Variant styles
          {
            // Default - Primary
            'bg-foreground text-background hover:bg-foreground/90':
              variant === 'default' || variant === 'destructive',
            // Secondary - Muted
            'bg-muted text-foreground hover:bg-muted/80':
              variant === 'secondary',
            // Outline - Border
            'border border-border bg-background hover:bg-muted hover:text-foreground':
              variant === 'outline',
            // Ghost - Transparent with hover
            'hover:bg-muted': variant === 'ghost',
            // Link - Text only
            'text-foreground underline-offset-4 hover:underline':
              variant === 'link',
          },
          // Size styles
          {
            'h-10 px-4 py-2': size === 'default',
            'h-8 rounded-md px-3 text-xs': size === 'sm',
            'h-11 rounded-md px-8': size === 'lg',
            'h-9 w-9 p-0 rounded-md': size === 'icon',
          },
          className
        )}
        ref={ref}
        disabled={isDisabled}
        {...props}
      >
        {loading && (
          <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
            <CircleNotch
              className={cn(
                'animate-spin',
                size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4',
                variant === 'default' || variant === 'destructive'
                  ? 'text-current opacity-70'
                  : 'text-foreground'
              )}
              weight="bold"
            />
          </span>
        )}
        <span
          className={cn(
            'flex items-center justify-center gap-2',
            loading && 'opacity-0'
          )}
        >
          {children}
        </span>
      </Comp>
    );
  }
);
Button.displayName = 'Button';

export { Button };
