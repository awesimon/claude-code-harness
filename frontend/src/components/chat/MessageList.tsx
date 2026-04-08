import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { ChatCircleText } from '@phosphor-icons/react';
import { Message } from './Message';
import { MessageSkeleton } from '@/components/ui/Skeleton';
import type { Message as MessageType } from '@/types';

interface MessageListProps {
  messages: MessageType[];
  isLoading?: boolean;
}

// Typing indicator component - isolated for performance
const TypingIndicator = React.memo(function TypingIndicator() {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="flex gap-3"
    >
      {/* Avatar */}
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full glass-strong bg-primary/20">
        <span className="sr-only">AI</span>
      </div>

      {/* Typing dots */}
      <div className="glass rounded-2xl px-4 py-3">
        <div className="flex items-center gap-1.5">
          {[0, 1, 2].map((i) => (
            <motion.div
              key={i}
              animate={shouldReduceMotion ? {} : {
                scale: [1, 1.3, 1],
                opacity: [0.4, 1, 0.4],
              }}
              transition={{
                repeat: Infinity,
                duration: 1.2,
                delay: i * 0.15,
                ease: 'easeInOut',
              }}
              className="h-2 w-2 rounded-full bg-primary"
            />
          ))}
        </div>
      </div>
    </motion.div>
  );
});

export const MessageList = React.forwardRef<HTMLDivElement, MessageListProps>(
  ({ messages, isLoading }, ref) => {
    const scrollRef = React.useRef<HTMLDivElement>(null);
    const shouldReduceMotion = useReducedMotion();

    // Auto-scroll to bottom with smooth behavior
    React.useEffect(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTo({
          top: scrollRef.current.scrollHeight,
          behavior: shouldReduceMotion ? 'auto' : 'smooth',
        });
      }
    }, [messages, isLoading, shouldReduceMotion]);

    // Empty state
    if (messages.length === 0) {
      return (
        <div
          ref={ref}
          className="flex h-full flex-col items-center justify-center px-4"
        >
          <motion.div
            initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, scale: 0.9, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="text-center"
          >
            {/* Logo Icon */}
            <motion.div
              className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl glass-strong bg-primary/20"
              whileHover={shouldReduceMotion ? {} : { scale: 1.05 }}
              transition={{ type: 'spring', stiffness: 400, damping: 25 }}
            >
              <ChatCircleText className="h-8 w-8 text-primary" weight="duotone" />
            </motion.div>

            <h2 className="mb-2 text-3xl font-semibold tracking-tight text-foreground">
              Claude Code
            </h2>
            <p className="max-w-md mx-auto text-muted-foreground leading-relaxed">
              A powerful AI assistant with advanced tool capabilities.
              Read files, write code, run commands, and more.
            </p>

            {/* Feature badges */}
            <div className="mt-8 flex flex-wrap justify-center gap-2">
              {['File Operations', 'Code Editing', 'Web Search', 'Command Execution'].map((feature, i) => (
                <motion.span
                  key={feature}
                  initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.2 + i * 0.1 }}
                  className="px-3 py-1 text-xs glass rounded-full text-muted-foreground"
                >
                  {feature}
                </motion.span>
              ))}
            </div>
          </motion.div>
        </div>
      );
    }

    return (
      <div
        ref={(node) => {
          (scrollRef as React.MutableRefObject<HTMLDivElement | null>).current = node;
          if (typeof ref === 'function') {
            ref(node);
          } else if (ref) {
            (ref as React.MutableRefObject<HTMLDivElement | null>).current = node;
          }
        }}
        className="flex-1 overflow-y-auto px-4 py-6 scrollbar-thin"
        aria-live="polite"
        aria-atomic="false"
      >
        <div className="mx-auto max-w-3xl space-y-6">
          <AnimatePresence mode="popLayout" initial={false}>
            {messages.map((message, index) => (
              <motion.div
                key={message.id}
                layout
                initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95 }}
                transition={{
                  duration: 0.3,
                  delay: shouldReduceMotion ? 0 : Math.min(index * 0.05, 0.3),
                  ease: [0.16, 1, 0.3, 1],
                }}
              >
                <Message message={message} isLast={index === messages.length - 1} />
              </motion.div>
            ))}
          </AnimatePresence>

          {/* Loading states */}
          <AnimatePresence>
            {isLoading && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
              >
                <TypingIndicator />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    );
  }
);
MessageList.displayName = 'MessageList';
