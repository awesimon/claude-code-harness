import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Brain, Wrench, FileText, MagnifyingGlass, Terminal, Globe } from '@phosphor-icons/react';
import type { ChatState } from '@/types';
import { cn } from '@/lib/utils';

interface StatusIndicatorProps {
  status: ChatState['status'];
}

// Tool icons mapping (unused but kept for future use)
const _toolIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  read_file: FileText,
  write_file: FileText,
  edit_file: FileText,
  bash: Terminal,
  glob: MagnifyingGlass,
  grep: MagnifyingGlass,
  web_search: Globe,
  web_fetch: Globe,
};

export function StatusIndicator({ status }: StatusIndicatorProps) {
  const shouldReduceMotion = useReducedMotion();

  const config = {
    idle: null,
    thinking: {
      icon: Brain,
      text: 'Thinking...',
      className: 'glass-strong border-primary/20 text-primary',
      iconClass: 'text-primary',
    },
    tool_calling: {
      icon: Wrench,
      text: 'Using tools...',
      className: 'glass-strong border-accent/20 text-accent',
      iconClass: 'text-accent',
    },
    streaming: null,
  };

  const current = config[status];

  if (!current) return null;

  const Icon = current.icon;

  return (
    <AnimatePresence>
      <motion.div
        initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: -10, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -10, scale: 0.95 }}
        transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
        className={cn(
          'flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium',
          current.className
        )}
      >
        <motion.div
          animate={shouldReduceMotion ? {} : { rotate: 360 }}
          transition={{
            repeat: Infinity,
            duration: 2,
            ease: 'linear',
          }}
        >
          <Icon className={cn('h-3.5 w-3.5', current.iconClass)} weight="duotone" />
        </motion.div>
        <span>{current.text}</span>
      </motion.div>
    </AnimatePresence>
  );
}
