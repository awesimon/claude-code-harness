import * as React from 'react';
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
  const hasContent = thinking.trim().length > 0;

  if (!hasContent) return null;

  // 格式化思考时间
  const formatThinkingTime = (ms?: number) => {
    if (!ms) return '';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  return (
    <div className="mb-3">
      <div
        className={cn(
          'overflow-hidden rounded-lg border transition-all duration-200',
          'bg-muted border-border'
        )}
      >
        {/* Header */}
        <button
          onClick={onToggle}
          className="flex w-full items-center justify-between px-3 py-2 text-left transition-colors hover:bg-muted/80"
        >
          <div className="flex items-center gap-2">
            <Brain className="h-4 w-4 text-muted-foreground" weight="duotone" />
            <span className="text-sm font-medium text-foreground">Thinking</span>
            {thinkingTime && (
              <span className="text-xs text-muted-foreground">
                ({formatThinkingTime(thinkingTime)})
              </span>
            )}
          </div>
          <CaretDown
            className={cn(
              'h-4 w-4 text-muted-foreground transition-transform duration-200',
              isExpanded && 'rotate-180'
            )}
          />
        </button>

        {/* Content */}
        {isExpanded && (
          <div className="overflow-hidden border-t border-border">
            <div className="max-h-96 overflow-y-auto px-3 py-2 bg-muted">
              <pre className="whitespace-pre-wrap break-words text-xs text-muted-foreground font-mono leading-relaxed">
                {thinking}
              </pre>
            </div>
          </div>
        )}

        {/* Collapsed Preview */}
        {!isExpanded && hasContent && (
          <div className="border-t border-border px-3 py-1.5 bg-muted">
            <p className="text-xs text-muted-foreground truncate">
              {thinking.slice(0, 100)}
              {thinking.length > 100 && '...'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
});

ThinkingBlock.displayName = 'ThinkingBlock';
