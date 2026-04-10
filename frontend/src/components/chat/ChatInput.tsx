import * as React from 'react';
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
  ({ onSend, onStop, disabled, isLoading, placeholder = 'Message...' }, ref) => {
    const [value, setValue] = React.useState('');
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);

    const handleSubmit = React.useCallback(() => {
      const trimmed = value.trim();
      if (trimmed && !disabled && !isLoading) {
        onSend(trimmed);
        setValue('');
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
        const newHeight = Math.min(Math.max(target.scrollHeight, 44), 200);
        target.style.height = `${newHeight}px`;
      },
      []
    );

    const hasContent = value.trim().length > 0;

    return (
      <div className="border-t border-border bg-background p-4">
        <div className="mx-auto max-w-3xl">
          <div className="flex items-end gap-2">
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
              placeholder={disabled ? 'Connecting...' : placeholder}
              disabled={disabled || isLoading}
              rows={1}
              className={cn(
                'min-h-[44px] resize-none rounded-md border-border bg-muted',
                'px-3 py-2.5 text-sm',
                'placeholder:text-muted-foreground',
                'focus-visible:border-foreground focus-visible:ring-0'
              )}
            />

            {isLoading ? (
              <Button
                size="icon"
                variant="outline"
                onClick={onStop}
                className="h-10 w-10 shrink-0 rounded-md"
                aria-label="Stop generating"
              >
                <Stop className="h-4 w-4" weight="fill" />
              </Button>
            ) : (
              <Button
                size="icon"
                onClick={handleSubmit}
                disabled={!hasContent || disabled}
                className={cn(
                  'h-10 w-10 shrink-0 rounded-md',
                  hasContent && !disabled
                    ? 'bg-foreground text-background hover:bg-foreground/90'
                    : 'bg-muted text-muted-foreground'
                )}
                aria-label="Send message"
              >
                <PaperPlaneRight className="h-4 w-4" weight="fill" />
              </Button>
            )}
          </div>

          <p className="mt-2 text-center text-[10px] text-muted-foreground">
            Press Enter to send, Shift + Enter for new line
          </p>
        </div>
      </div>
    );
  }
);
ChatInput.displayName = 'ChatInput';
