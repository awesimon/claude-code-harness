import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Toolbox, CaretDown } from '@phosphor-icons/react';
import { ToolCall } from './ToolCall';
import { ToolResult } from './ToolResult';
import { useTools } from '@/hooks/useTools';
import type { ToolCall as ToolCallType, ToolResult as ToolResultType } from '@/types';
import { cn } from '@/lib/utils';

interface ToolPanelProps {
  toolCalls?: ToolCallType[];
  toolResults?: ToolResultType[];
}

export function ToolPanel({ toolCalls, toolResults }: ToolPanelProps) {
  const [isExpanded, setIsExpanded] = React.useState(true);
  const { toggleCall, toggleResult, isCallExpanded, isResultExpanded } = useTools();
  const shouldReduceMotion = useReducedMotion();

  const hasTools = (toolCalls && toolCalls.length > 0) || (toolResults && toolResults.length > 0);

  if (!hasTools) return null;

  const totalTools = (toolCalls?.length || 0) + (toolResults?.length || 0);

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className="mb-4 overflow-hidden rounded-xl glass border border-accent/20"
    >
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-white/5"
      >
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-accent/20">
            <Toolbox className="h-3.5 w-3.5 text-accent" weight="duotone" />
          </div>
          <span className="text-sm font-medium text-foreground">Tool Calls</span>
          <span className="flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-accent/20 px-1.5 text-xs font-medium text-accent">
            {totalTools}
          </span>
        </div>
        <motion.div
          animate={{ rotate: isExpanded ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >
          <CaretDown className="h-4 w-4 text-muted-foreground" />
        </motion.div>
      </button>

      {/* Content */}
      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: 'auto' }}
            exit={{ height: 0 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="space-y-3 border-t border-white/5 p-4">
              {toolCalls?.map((toolCall) => (
                <ToolCall
                  key={toolCall.id}
                  toolCall={toolCall}
                  isExpanded={isCallExpanded(toolCall.id)}
                  onToggle={() => toggleCall(toolCall.id)}
                />
              ))}
              {toolResults?.map((toolResult, index) => (
                <ToolResult
                  key={`${toolResult.name}-${index}`}
                  toolResult={toolResult}
                  isExpanded={isResultExpanded(toolResult.name)}
                  onToggle={() => toggleResult(toolResult.name)}
                />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
