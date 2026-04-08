import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion, useSpring } from 'framer-motion';
import { PaperPlaneRight, Stop, Command } from '@phosphor-icons/react';
import { Textarea } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { MagneticButton } from '@/components/ui/MagneticButton';
import { cn } from '@/lib/utils';
import { useChatStore } from '@/stores/chatStore';

interface ChatInputProps {
  onSend: (message: string) => void;
  onStop?: () => void;
  disabled?: boolean;
  isLoading?: boolean;
  placeholder?: string;
}

export const ChatInput = React.forwardRef<HTMLTextAreaElement, ChatInputProps>(
  ({ onSend, onStop, disabled, isLoading, placeholder = 'Message Claude...' }, ref) => {
    const [value, setValue] = React.useState('');
    const [isFocused, setIsFocused] = React.useState(false);
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const shouldReduceMotion = useReducedMotion();
    const { connectionStatus } = useChatStore();

    // Smooth height animation
    const heightSpring = useSpring(52, {
      stiffness: 400,
      damping: 30,
      mass: 0.8,
    });

    const handleSubmit = React.useCallback(() => {
      const trimmed = value.trim();
      if (trimmed && !disabled && !isLoading) {
        onSend(trimmed);
        setValue('');
        // Reset textarea height with animation
        heightSpring.set(52);
        if (textareaRef.current) {
          textareaRef.current.style.height = 'auto';
        }
      }
    }, [value, disabled, isLoading, onSend, heightSpring]);

    const handleKeyDown = React.useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // Support both Cmd+Enter and plain Enter (without Shift)
        if ((e.key === 'Enter' && (e.metaKey || e.ctrlKey)) || (e.key === 'Enter' && !e.shiftKey)) {
          e.preventDefault();
          handleSubmit();
        }
      },
      [handleSubmit]
    );

    const handleInput = React.useCallback(
      (e: React.FormEvent<HTMLTextAreaElement>) => {
        const target = e.currentTarget;
        target.style.height = 'auto';
        const newHeight = Math.min(Math.max(target.scrollHeight, 52), 200);
        target.style.height = `${newHeight}px`;
        if (!shouldReduceMotion) {
          heightSpring.set(newHeight);
        }
      },
      [heightSpring, shouldReduceMotion]
    );

    const hasContent = value.trim().length > 0;

    return (
      <motion.div
        initial={{ y: shouldReduceMotion ? 0 : 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
        className="relative"
      >
        {/* Glass container with animated border */}
        <div
          className={cn(
            'relative overflow-hidden rounded-2xl border bg-card/80 backdrop-blur-xl',
            'transition-all duration-300 ease-out',
            isFocused
              ? 'border-primary/40 shadow-lg shadow-primary/10 ring-1 ring-primary/20'
              : 'border-white/10 shadow-lg shadow-black/5'
          )}
        >
          {/* Animated gradient background on focus */}
          <motion.div
            className="absolute inset-0 bg-gradient-to-r from-primary/5 via-transparent to-primary/5"
            initial={{ opacity: 0 }}
            animate={{ opacity: isFocused ? 1 : 0 }}
            transition={{ duration: 0.3 }}
          />

          <div className="relative flex items-end gap-3 p-3">
            {/* Textarea */}
            <div className="relative flex-1">
              <Textarea
                ref={(node) => {
                  (textareaRef as React.MutableRefObject<HTMLTextAreaElement | null>).current = node;
                  if (typeof ref === 'function') {
                    ref(node);
                  } else if (ref) {
                    (ref as React.MutableRefObject<HTMLTextAreaElement | null>).current = node;
                  }
                }}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={handleKeyDown}
                onInput={handleInput}
                onFocus={() => setIsFocused(true)}
                onBlur={() => setIsFocused(false)}
                placeholder={disabled ? 'Connecting...' : placeholder}
                disabled={disabled || isLoading}
                rows={1}
                className={cn(
                  'min-h-[44px] resize-none border-0 bg-transparent px-2 py-2.5 text-sm',
                  'placeholder:text-muted-foreground/60',
                  'focus-visible:ring-0 focus-visible:ring-offset-0',
                  isLoading && 'opacity-60'
                )}
              />
            </div>

            {/* Send/Stop button with magnetic effect */}
            <div className="flex shrink-0 items-end pb-1">
              <AnimatePresence mode="wait">
                {isLoading ? (
                  <motion.div
                    key="stop"
                    initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, scale: 0.8, y: 10 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.8, y: -10 }}
                    transition={{
                      duration: 0.2,
                      ease: [0.22, 1, 0.36, 1],
                    }}
                  >
                    <Button
                      size="icon"
                      variant="destructive"
                      onClick={onStop}
                      className="h-10 w-10 rounded-xl shadow-lg shadow-destructive/25"
                      aria-label="Stop generating"
                    >
                      <Stop className="h-4 w-4" weight="fill" />
                    </Button>
                  </motion.div>
                ) : (
                  <motion.div
                    key="send"
                    initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, scale: 0.8, y: 10 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.8, y: -10 }}
                    transition={{
                      duration: 0.2,
                      ease: [0.22, 1, 0.36, 1],
                    }}
                  >
                    <MagneticButton strength={0.4} disabled={!hasContent || disabled}>
                      <Button
                        size="icon"
                        onClick={handleSubmit}
                        disabled={!hasContent || disabled}
                        className={cn(
                          'h-10 w-10 rounded-xl transition-all duration-300',
                          hasContent && !disabled
                            ? 'bg-primary text-primary-foreground shadow-lg shadow-primary/30 hover:shadow-primary/50 hover:brightness-110'
                            : 'bg-muted text-muted-foreground'
                        )}
                        aria-label="Send message"
                      >
                        <motion.div
                          animate={
                            hasContent && !disabled
                              ? { x: [0, 2, 0], y: [0, -2, 0] }
                              : { x: 0, y: 0 }
                          }
                          transition={{
                            duration: 1.5,
                            repeat: Infinity,
                            repeatType: 'loop',
                            ease: 'easeInOut',
                          }}
                        >
                          <PaperPlaneRight className="h-4 w-4" weight="fill" />
                        </motion.div>
                      </Button>
                    </MagneticButton>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </div>

        {/* Helper text with keyboard shortcut hint */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5, duration: 0.3 }}
          className="mt-2 flex items-center justify-center gap-4 px-4"
        >
          <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground/50">
            <kbd className="inline-flex h-4 min-h-4 items-center justify-center rounded bg-white/5 px-1.5 font-mono text-[9px] text-muted-foreground/60">
              <Command className="mr-0.5 h-2.5 w-2.5" />
              Enter
            </kbd>
            to send
          </p>
          <span className="text-muted-foreground/30">·</span>
          <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground/50">
            <kbd className="inline-flex h-4 min-h-4 items-center justify-center rounded bg-white/5 px-1.5 font-mono text-[9px] text-muted-foreground/60">
              Shift
            </kbd>
            +
            <kbd className="inline-flex h-4 min-h-4 items-center justify-center rounded bg-white/5 px-1.5 font-mono text-[9px] text-muted-foreground/60">
              Enter
            </kbd>
            for new line
          </p>
        </motion.div>
      </motion.div>
    );
  }
);
ChatInput.displayName = 'ChatInput';
