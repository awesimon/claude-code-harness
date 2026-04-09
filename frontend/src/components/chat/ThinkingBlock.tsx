import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Brain, CaretDown } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

interface ThinkingBlockProps {
  thinking: string;
  thinkingTime?: number;
  isExpanded?: boolean;
  onToggle?: () => void;
}

export const ThinkingBlock = React.memo(function ThinkingBlock({
  thinking,
  thinkingTime,
  isExpanded = false,
  onToggle,
}: ThinkingBlockProps) {
  const shouldReduceMotion = useReducedMotion();
  const hasContent = thinking.trim().length > 0;

  if (!hasContent) return null;

  // 格式化思考时间
  const formatThinkingTime = (ms?: number) => {
    if (!ms) return '';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="mb-3"
    >
      <div
        className={cn(
          'overflow-hidden rounded-xl border transition-all duration-200',
          'bg-amber-500/5 border-amber-500/20',
          isExpanded && 'bg-amber-500/10'
        )}
      >
        {/* Header */}
        <button
          onClick={onToggle}
          className="flex w-full items-center justify-between px-3 py-2 text-left transition-colors hover:bg-amber-500/10"
        >
          <div className="flex items-center gap-2">
            <Brain className="h-4 w-4 text-amber-400" weight="duotone" />
            <span className="text-sm font-medium text-amber-400">Thinking</span>
            {thinkingTime && (
              <span className="text-xs text-amber-400/60">
                ({formatThinkingTime(thinkingTime)})
              </span>
            )}
          </div>
          <motion.div
            animate={{ rotate: isExpanded ? 180 : 0 }}
            transition={{ duration: shouldReduceMotion ? 0 : 0.2 }}
          >
            <CaretDown className="h-4 w-4 text-amber-400/60" />
          </motion.div>
        </button>

        {/* Content */}
        <AnimatePresence initial={false}>
          {isExpanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{
                duration: shouldReduceMotion ? 0 : 0.2,
                ease: [0.16, 1, 0.3, 1],
              }}
              className="overflow-hidden"
            >
              <div className="border-t border-amber-500/10">
                <div className="max-h-96 overflow-y-auto px-3 py-2">
                  <pre className="whitespace-pre-wrap break-words text-xs text-amber-200/80 font-mono leading-relaxed">
                    {thinking}
                  </pre>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Collapsed Preview */}
        {!isExpanded && hasContent && (
          <div className="border-t border-amber-500/10 px-3 py-1.5">
            <p className="text-xs text-amber-200/50 truncate">
              {thinking.slice(0, 100)}
              {thinking.length > 100 && '...'}
            </p>
          </div>
        )}
      </div>
    </motion.div>
  );
});

ThinkingBlock.displayName = 'ThinkingBlock';
