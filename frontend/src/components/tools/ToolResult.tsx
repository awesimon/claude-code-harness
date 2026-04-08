import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Check, X, CaretDown } from '@phosphor-icons/react';
import type { ToolResult as ToolResultType } from '@/types';
import { cn, truncateText } from '@/lib/utils';

interface ToolResultProps {
  toolResult: ToolResultType;
  isExpanded?: boolean;
  onToggle?: () => void;
}

const MAX_PREVIEW_LENGTH = 300;

export const ToolResult = React.memo(function ToolResult({
  toolResult,
  isExpanded = false,
  onToggle,
}: ToolResultProps) {
  const shouldReduceMotion = useReducedMotion();

  const formattedResult = React.useMemo(() => {
    try {
      if (typeof toolResult.result === 'string') {
        return toolResult.result;
      }
      return JSON.stringify(toolResult.result, null, 2);
    } catch {
      return String(toolResult.result);
    }
  }, [toolResult.result]);

  const isTruncated = formattedResult.length > MAX_PREVIEW_LENGTH;
  const displayContent = isTruncated && !isExpanded
    ? truncateText(formattedResult, MAX_PREVIEW_LENGTH)
    : formattedResult;

  const isSuccess = toolResult.success;

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
      className="flex gap-3"
    >
      {/* Spacer for alignment */}
      <div className="w-7 shrink-0" />

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div
          className={cn(
            'glass overflow-hidden rounded-xl',
            isSuccess ? 'border border-emerald-500/20' : 'border border-destructive/20'
          )}
        >
          {/* Header */}
          <button
            onClick={onToggle}
            className="flex w-full items-center justify-between px-3 py-2.5 text-left transition-colors hover:bg-white/5"
          >
            <div className="flex items-center gap-2">
              {isSuccess ? (
                <div className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20">
                  <Check className="h-3 w-3 text-emerald-500" weight="bold" />
                </div>
              ) : (
                <div className="flex h-5 w-5 items-center justify-center rounded-full bg-destructive/20">
                  <X className="h-3 w-3 text-destructive" weight="bold" />
                </div>
              )}
              <span
                className={cn(
                  'text-sm font-medium',
                  isSuccess ? 'text-emerald-500' : 'text-destructive'
                )}
              >
                {toolResult.name}
              </span>
              <span className="text-xs text-muted-foreground">
                {isSuccess ? 'Success' : 'Error'}
              </span>
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
                    <code>{formattedResult}</code>
                  </pre>
                  {isTruncated && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      Result truncated. Click to collapse.
                    </p>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Preview when collapsed and truncated */}
          {!isExpanded && isTruncated && (
            <div className="border-t border-white/5 px-3 py-2">
              <pre className="overflow-x-auto text-xs text-muted-foreground scrollbar-thin">
                <code>{displayContent}</code>
              </pre>
              {isTruncated && (
                <p className="mt-1 text-[10px] text-muted-foreground/50">
                  Click to expand
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
});
