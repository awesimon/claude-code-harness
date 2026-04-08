import * as React from 'react';
import { cn } from '@/lib/utils';

export interface InputProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {}

const Textarea = React.forwardRef<HTMLTextAreaElement, InputProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          // Base styles
          'flex w-full rounded-2xl px-4 py-3 text-sm text-foreground',
          'placeholder:text-muted-foreground',
          'glass-strong',
          // Focus state with glow
          'focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary/50',
          'focus:shadow-lg focus:shadow-primary/10',
          'transition-all duration-200',
          // Disabled state
          'disabled:cursor-not-allowed disabled:opacity-50',
          // Textarea specific
          'min-h-[52px] resize-none leading-relaxed',
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);
Textarea.displayName = 'Textarea';

export interface SimpleInputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, SimpleInputProps>(
  ({ className, ...props }, ref) => {
    return (
      <input
        className={cn(
          'flex h-10 w-full rounded-lg px-3 py-2 text-sm text-foreground',
          'placeholder:text-muted-foreground',
          'glass',
          'focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary/50',
          'transition-all duration-200',
          'disabled:cursor-not-allowed disabled:opacity-50',
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);
Input.displayName = 'Input';

export { Textarea, Input };
