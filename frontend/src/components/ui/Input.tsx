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
          'flex w-full rounded-md px-3 py-2 text-sm text-foreground',
          'placeholder:text-muted-foreground',
          'bg-muted border border-border',
          // Focus state
          'focus:outline-none focus:border-foreground',
          'transition-colors duration-200',
          // Disabled state
          'disabled:cursor-not-allowed disabled:opacity-50',
          // Textarea specific
          'min-h-[44px] resize-none leading-relaxed',
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
          'flex h-10 w-full rounded-md px-3 py-2 text-sm text-foreground',
          'placeholder:text-muted-foreground',
          'bg-muted border border-border',
          'focus:outline-none focus:border-foreground',
          'transition-colors duration-200',
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
