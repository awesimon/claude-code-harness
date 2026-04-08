import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { PaperPlaneRight, Stop } from '@phosphor-icons/react';
import { Textarea } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/utils';

interface ChatInputProps {
  onSend: (message: string) => void;
  onStop?: () => void;
  disabled?: boolean;
  isLoading?: boolean;
  placeholder?: string;
}

export const ChatInput = React.forwardRef<HTMLTextAreaElement, ChatInputProps>(
  ({ onSend, onStop, disabled, isLoading, placeholder = 'Message Claude... (Shift+Enter for new line)' }, ref) => {
    const [value, setValue] = React.useState('');
    const [isFocused, setIsFocused] = React.useState(false);
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const shouldReduceMotion = useReducedMotion();

    const handleSubmit = React.useCallback(() => {
      const trimmed = value.trim();
      if (trimmed && !disabled && !isLoading) {
        onSend(trimmed);
        setValue('');
        // Reset textarea height
        if (textareaRef.current) {
          textareaRef.current.style.height = 'auto';
        }
      }
    }, [value, disabled, isLoading, onSend]);

    const handleKeyDown = React.useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
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
        target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
      },
      []
    );

    const hasContent = value.trim().length > 0;

    return (
      <div className="glass border-t border-white/5 px-4 py-4">
        <div className="mx-auto max-w-3xl">
          <div className="flex gap-3">
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
                  'min-h-[52px] resize-none pr-14 transition-all duration-200',
                  isLoading && 'opacity-70',
                  isFocused && 'ring-2 ring-primary/30'
                )}
              />

              <div className="absolute bottom-2 right-2">
                <AnimatePresence mode="wait">
                  {isLoading ? (
                    <motion.div
                      key="stop"
                      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, scale: 0.8 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.8 }}
                      transition={{ duration: 0.15 }}
                    >
                      <Button
                        size="icon"
                        variant="destructive"
                        onClick={onStop}
                        className="h-9 w-9"
                        aria-label="Stop generating"
                      >
                        <Stop className="h-4 w-4" weight="fill" />
                      </Button>
                    </motion.div>
                  ) : (
                    <motion.div
                      key="send"
                      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, scale: 0.8 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.8 }}
                      transition={{ duration: 0.15 }}
                    >
                      <Button
                        size="icon"
                        onClick={handleSubmit}
                        disabled={!hasContent || disabled}
                        className={cn(
                          'h-9 w-9 transition-all duration-200',
                          hasContent && !disabled
                            ? 'bg-primary text-primary-foreground shadow-lg shadow-primary/25 hover:shadow-primary/40'
                            : 'bg-white/5 text-muted-foreground'
                        )}
                        aria-label="Send message"
                      >
                        <PaperPlaneRight
                          className={cn(
                            'h-4 w-4 transition-transform duration-200',
                            hasContent && 'translate-x-0.5 -translate-y-0.5'
                          )}
                          weight="fill"
                        />
                      </Button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>

          {/* Helper text */}
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mt-2 text-center text-[10px] text-muted-foreground/50"
          >
            Supports: read_file, write_file, edit_file, bash, glob, grep, web_search, web_fetch
          </motion.p>
        </div>
      </div>
    );
  }
);
ChatInput.displayName = 'ChatInput';
