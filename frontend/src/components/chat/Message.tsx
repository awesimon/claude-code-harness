import * as React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { User, Robot } from '@phosphor-icons/react';
import { marked } from 'marked';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.min.css';
import type { MessageProps } from '@/types';
import { cn } from '@/lib/utils';
import { ToolCall } from '@/components/tools/ToolCall';
import { ToolResult } from '@/components/tools/ToolResult';

// Configure marked for security and features
marked.setOptions({
  breaks: true,
  gfm: true,
});

export const Message = React.memo(function Message({ message, isLast }: MessageProps) {
  const contentRef = React.useRef<HTMLDivElement>(null);
  const isUser = message.role === 'user';
  const shouldReduceMotion = useReducedMotion();

  // Track expanded states for tool calls and results
  const [expandedTools, setExpandedTools] = React.useState<Set<string>>(new Set());

  const toggleTool = React.useCallback((id: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  // Highlight code blocks when content changes
  React.useEffect(() => {
    if (contentRef.current) {
      contentRef.current.querySelectorAll('pre code').forEach((block) => {
        hljs.highlightElement(block as HTMLElement);
      });
    }
  }, [message.content]);

  const formattedContent = React.useMemo(() => {
    return marked.parse(message.content);
  }, [message.content]);

  // Animation variants
  const containerVariants = {
    initial: shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 20 },
    animate: { opacity: 1, y: 0 },
  };

  const avatarVariants = {
    initial: shouldReduceMotion ? { opacity: 1 } : { scale: 0.8, opacity: 0 },
    animate: { scale: 1, opacity: 1 },
  };

  const contentVariants = {
    initial: shouldReduceMotion ? { opacity: 1 } : { opacity: 0, x: isUser ? 20 : -20 },
    animate: { opacity: 1, x: 0 },
  };

  const timestampVariants = {
    initial: shouldReduceMotion ? { opacity: 1 } : { opacity: 0 },
    animate: { opacity: 1 },
  };

  return (
    <motion.div
      variants={containerVariants}
      initial="initial"
      animate="animate"
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className={cn('flex gap-3', isUser ? 'flex-row-reverse' : 'flex-row')}
    >
      {/* Avatar - Glass effect */}
      <motion.div
        variants={avatarVariants}
        initial="initial"
        animate="animate"
        transition={{ delay: shouldReduceMotion ? 0 : 0.1 }}
        className={cn(
          'flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-full glass-strong',
          isUser ? 'bg-secondary' : 'bg-primary/20'
        )}
      >
        {isUser ? (
          <User className="h-4 w-4 text-foreground" weight="bold" />
        ) : (
          <Robot className="h-4 w-4 text-primary" weight="bold" />
        )}
      </motion.div>

      {/* Content Column */}
      <div className={cn('flex-1 min-w-0', isUser ? 'text-right' : 'text-left')}>
        {/* Tool Calls */}
        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mb-3 space-y-2">
            {message.toolCalls.map((toolCall) => (
              <ToolCall
                key={toolCall.id}
                toolCall={toolCall}
                isExpanded={expandedTools.has(toolCall.id)}
                onToggle={() => toggleTool(toolCall.id)}
              />
            ))}
          </div>
        )}

        {/* Message Content */}
        {message.content && (
          <motion.div
            variants={contentVariants}
            initial="initial"
            animate="animate"
            transition={{ delay: shouldReduceMotion ? 0 : 0.15, duration: 0.25 }}
            className={cn(
              'inline-block max-w-[90%] rounded-2xl px-4 py-3 text-left tap-highlight',
              isUser
                ? 'bg-primary text-primary-foreground'
                : 'glass border border-white/10'
            )}
          >
            <div
              ref={contentRef}
              className="message-content"
              dangerouslySetInnerHTML={{ __html: formattedContent }}
            />
          </motion.div>
        )}

        {/* Tool Results */}
        {message.toolResults && message.toolResults.length > 0 && (
          <div className={cn('mt-3 space-y-2', isUser ? 'text-right' : 'text-left')}>
            {message.toolResults.map((toolResult, index) => (
              <ToolResult
                key={`${toolResult.name}-${index}`}
                toolResult={toolResult}
                isExpanded={expandedTools.has(`result-${index}`)}
                onToggle={() => toggleTool(`result-${index}`)}
              />
            ))}
          </div>
        )}

        {/* Timestamp */}
        <motion.div
          variants={timestampVariants}
          initial="initial"
          animate="animate"
          transition={{ delay: shouldReduceMotion ? 0 : 0.3 }}
          className="mt-1 text-xs text-muted-foreground"
        >
          {new Date(message.timestamp).toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </motion.div>
      </div>
    </motion.div>
  );
});
