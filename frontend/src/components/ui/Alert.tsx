import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X, Warning, CheckCircle, Info } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

export interface AlertProps {
  variant?: 'default' | 'destructive' | 'success' | 'warning';
  title?: string;
  children: React.ReactNode;
  onDismiss?: () => void;
  className?: string;
}

const variantConfig = {
  default: {
    container: 'glass border-white/10 text-foreground',
    icon: Info,
    iconClass: 'text-primary',
  },
  destructive: {
    container: 'bg-destructive/10 border-destructive/20 text-destructive',
    icon: Warning,
    iconClass: 'text-destructive',
  },
  success: {
    container: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-500',
    icon: CheckCircle,
    iconClass: 'text-emerald-500',
  },
  warning: {
    container: 'bg-amber-500/10 border-amber-500/20 text-amber-500',
    icon: Warning,
    iconClass: 'text-amber-500',
  },
};

export function Alert({
  variant = 'default',
  title,
  children,
  onDismiss,
  className,
}: AlertProps) {
  const shouldReduceMotion = useReducedMotion();
  const config = variantConfig[variant];
  const Icon = config.icon;

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: -10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -10, scale: 0.98 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        'relative rounded-xl border p-4',
        config.container,
        className
      )}
      role="alert"
    >
      <div className="flex gap-3">
        <Icon className={cn('h-5 w-5 shrink-0 mt-0.5', config.iconClass)} weight="fill" />
        <div className="flex-1 min-w-0">
          {title && (
            <h5 className="mb-1 font-medium leading-none tracking-tight">{title}</h5>
          )}
          <div className="text-sm opacity-90">{children}</div>
        </div>
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="shrink-0 rounded-lg p-1 opacity-70 transition-opacity hover:opacity-100 hover:bg-white/5"
            aria-label="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
    </motion.div>
  );
}

export interface ErrorAlertProps {
  error: string | null;
  onDismiss?: () => void;
}

export function ErrorAlert({ error, onDismiss }: ErrorAlertProps) {
  return (
    <AnimatePresence mode="wait">
      {error && (
        <motion.div
          key="error"
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          className="overflow-hidden"
        >
          <Alert variant="destructive" onDismiss={onDismiss} className="mb-4">
            {error}
          </Alert>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
