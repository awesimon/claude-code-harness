import * as React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { FolderOpen, MagnifyingGlass, FileText, Terminal, Sparkle } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

interface QuickAction {
  icon: React.ReactNode;
  title: string;
  description: string;
  command: string;
  color: string;
}

const quickActions: QuickAction[] = [
  {
    icon: <FolderOpen className="h-5 w-5" weight="duotone" />,
    title: 'Explore Files',
    description: 'View directory structure with glob',
    command: 'Help me explore the project file structure',
    color: 'text-blue-400',
  },
  {
    icon: <MagnifyingGlass className="h-5 w-5" weight="duotone" />,
    title: 'Search Code',
    description: 'Find code patterns with grep',
    command: 'Search for TODO or FIXME comments in the codebase',
    color: 'text-emerald-400',
  },
  {
    icon: <FileText className="h-5 w-5" weight="duotone" />,
    title: 'Read Files',
    description: 'Open and analyze any file',
    command: 'Read the main configuration file and explain it',
    color: 'text-amber-400',
  },
  {
    icon: <Terminal className="h-5 w-5" weight="duotone" />,
    title: 'Run Commands',
    description: 'Execute bash commands safely',
    command: 'Show me the current git status',
    color: 'text-rose-400',
  },
];

interface QuickActionsProps {
  onSelect: (command: string) => void;
}

export function QuickActions({ onSelect }: QuickActionsProps) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <div className="space-y-4">
      {/* Header */}
      <motion.div
        initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center justify-center gap-2 text-muted-foreground"
      >
        <Sparkle className="h-4 w-4" />
        <span className="text-xs font-medium uppercase tracking-wider">Quick Actions</span>
      </motion.div>

      {/* Actions Grid */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {quickActions.map((action, index) => (
          <motion.button
            key={action.title}
            initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{
              delay: shouldReduceMotion ? 0 : index * 0.08,
              duration: 0.3,
              ease: [0.16, 1, 0.3, 1],
            }}
            whileHover={shouldReduceMotion ? {} : { scale: 1.02, y: -2 }}
            whileTap={{ scale: 0.98 }}
            onClick={() => onSelect(action.command)}
            className={cn(
              'group relative overflow-hidden rounded-xl border border-white/5 p-4 text-left transition-all duration-300',
              'glass hover:border-white/10 hover:bg-white/[0.02]',
              'tap-highlight'
            )}
          >
            {/* Subtle gradient overlay on hover */}
            <div className="absolute inset-0 bg-gradient-to-br from-white/[0.02] to-transparent opacity-0 transition-opacity group-hover:opacity-100" />

            <div className="relative">
              <div className="mb-3 flex items-center gap-2">
                <div className={cn('rounded-lg bg-white/5 p-2', action.color)}>
                  {action.icon}
                </div>
              </div>
              <h3 className="mb-1 text-sm font-medium text-foreground">{action.title}</h3>
              <p className="text-xs text-muted-foreground">{action.description}</p>
            </div>
          </motion.button>
        ))}
      </div>
    </div>
  );
}
