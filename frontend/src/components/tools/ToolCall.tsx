import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Wrench, CaretDown, FileText, Command, Globe, MagnifyingGlass, Terminal } from '@phosphor-icons/react';
import type { ToolCall as ToolCallType } from '@/types';
import { cn } from '@/lib/utils';

interface ToolCallProps {
  toolCall: ToolCallType;
  isExpanded?: boolean;
  onToggle?: () => void;
}

// Tool icon mapping
const toolIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  read_file: FileText,
  write_file: FileText,
  edit_file: FileText,
  bash: Terminal,
  glob: MagnifyingGlass,
  grep: MagnifyingGlass,
  web_search: Globe,
  web_fetch: Globe,
};

export const ToolCall = React.memo(function ToolCall({
  toolCall,
  isExpanded = false,
  onToggle,
}: ToolCallProps) {
  const shouldReduceMotion = useReducedMotion();

  const formattedArgs = React.useMemo(() => {
    try {
      return JSON.stringify(toolCall.arguments, null, 2);
    } catch {
      return String(toolCall.arguments);
    }
  }, [toolCall.arguments]);

  // Get appropriate icon
  const IconComponent = toolIcons[toolCall.name] || Wrench;

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className="flex gap-3"
    >
      {/* Tool Icon Avatar */}
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full glass-strong bg-accent/20">
        <IconComponent className="h-3.5 w-3.5 text-accent" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="glass overflow-hidden rounded-xl border border-accent/20">
          {/* Header */}
          <button
            onClick={onToggle}
            className="flex w-full items-center justify-between px-3 py-2.5 text-left transition-colors hover:bg-white/5"
          >
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-accent">
                {toolCall.name}
              </span>
              <span className="text-xs text-muted-foreground">Tool Call</span>
            </div>
            <motion.div
              animate={{ rotate: isExpanded ? 180 : 0 }}
              transition={{ duration: 0.2 }}
            >
              <CaretDown className="h-4 w-4 text-muted-foreground" />
            </motion.div>
          </button>

          {/* Expanded Content */}
          <AnimatePresence>
            {isExpanded && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
                className="overflow-hidden"
              >
                <div className="border-t border-white/5 px-3 py-3">
                  <pre className="overflow-x-auto rounded-lg bg-black/30 p-3 text-xs text-foreground/80 scrollbar-thin">
                    <code>{formattedArgs}</code>
                  </pre>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
});
