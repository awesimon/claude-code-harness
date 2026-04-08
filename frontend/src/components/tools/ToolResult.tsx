import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  Check,
  X,
  CaretDown,
  Copy,
  CheckCircle,
  Warning,
  FileJs,
  TextT,
  Download,
  ArrowsOutSimple,
  ArrowsInSimple,
} from '@phosphor-icons/react';
import type { ToolResult as ToolResultType } from '@/types';
import { cn, truncateText } from '@/lib/utils';

interface ToolResultProps {
  toolResult: ToolResultType;
  isExpanded?: boolean;
  onToggle?: () => void;
  className?: string;
}

type ViewMode = 'preview' | 'json' | 'raw';

const MAX_PREVIEW_LENGTH = 500;
const MAX_INLINE_LENGTH = 200;

// Syntax highlighting for JSON
function highlightJson(json: string): string {
  return json
    .replace(/"(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"/g, '<span class="text-green-400">$&</span>')
    .replace(/\b(true|false|null)\b/g, '<span class="text-purple-400">$&</span>')
    .replace(/\b(\d+\.?\d*)\b/g, '<span class="text-amber-400">$&</span>')
    .replace(/[{\[}\]]/g, '<span class="text-cyan-400">$&</span>')
    .replace(/:/g, '<span class="text-muted-foreground">:</span>');
}

export const ToolResult = React.memo(function ToolResult({
  toolResult,
  isExpanded = false,
  onToggle,
  className,
}: ToolResultProps) {
  const shouldReduceMotion = useReducedMotion();
  const [viewMode, setViewMode] = React.useState<ViewMode>('preview');
  const [isCopied, setIsCopied] = React.useState(false);
  const [isFullscreen, setIsFullscreen] = React.useState(false);

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

  const jsonFormatted = React.useMemo(() => {
    try {
      if (typeof toolResult.result === 'string') {
        // Try to parse as JSON first
        const parsed = JSON.parse(toolResult.result);
        return JSON.stringify(parsed, null, 2);
      }
      return JSON.stringify(toolResult.result, null, 2);
    } catch {
      return formattedResult;
    }
  }, [toolResult.result, formattedResult]);

  const resultSize = formattedResult.length;
  const isLarge = resultSize > MAX_PREVIEW_LENGTH;
  const isTruncated = isLarge && !isExpanded;

  const displayContent = React.useMemo(() => {
    if (isTruncated) {
      return truncateText(formattedResult, MAX_PREVIEW_LENGTH);
    }
    return formattedResult;
  }, [formattedResult, isTruncated]);

  const isSuccess = toolResult.success;
  const hasError = !isSuccess && toolResult.error;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(formattedResult);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch {
      // Silent fail
    }
  };

  const handleDownload = () => {
    const blob = new Blob([formattedResult], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${toolResult.name}-result.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const renderContent = () => {
    switch (viewMode) {
      case 'json':
        return (
          <pre
            className="overflow-x-auto p-3 text-xs font-mono leading-relaxed
                       scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent"
            dangerouslySetInnerHTML={{ __html: highlightJson(jsonFormatted) }}
          />
        );
      case 'raw':
        return (
          <pre
            className="overflow-x-auto p-3 text-xs text-foreground/80
                       scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent"
          >
            <code>{formattedResult}</code>
          </pre>
        );
      case 'preview':
      default:
        if (typeof toolResult.result === 'object' && toolResult.result !== null) {
          return (
            <div className="p-3 space-y-2">
              {Object.entries(toolResult.result as Record<string, unknown>)
                .slice(0, isExpanded ? undefined : 5)
                .map(([key, value]) => (
                  <div key={key} className="flex items-start gap-2 text-sm">
                    <span className="text-muted-foreground font-mono">{key}:</span>
                    <span className="text-foreground/80 break-all">
                      {typeof value === 'object'
                        ? JSON.stringify(value).slice(0, 100) +
                          (JSON.stringify(value).length > 100 ? '...' : '')
                        : String(value).slice(0, 100) +
                          (String(value).length > 100 ? '...' : '')}
                    </span>
                  </div>
                ))}
              {!isExpanded &&
                Object.keys(toolResult.result as Record<string, unknown>).length > 5 && (
                  <div className="text-xs text-muted-foreground">
                    +
                    {Object.keys(toolResult.result as Record<string, unknown>).length - 5}
                    {' '}more fields...
                  </div>
                )}
            </div>
          );
        }
        return (
          <pre
            className="overflow-x-auto p-3 text-xs text-foreground/80
                       scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent"
          >
            <code>{displayContent}</code>
          </pre>
        );
    }
  };

  const getResultStats = () => {
    if (typeof toolResult.result === 'object' && toolResult.result !== null) {
      const keys = Object.keys(toolResult.result as Record<string, unknown>);
      return `${keys.length} fields`;
    }
    if (typeof toolResult.result === 'string') {
      const lines = toolResult.result.split('\n').length;
      return `${lines} line${lines !== 1 ? 's' : ''}, ${resultSize} chars`;
    }
    return `${resultSize} chars`;
  };

  return (
    <>
      <motion.div
        initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.3, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
        className={cn('flex gap-3', className)}
      >
        {/* Spacer for alignment */}
        <div className="w-8 shrink-0" />

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div
            className={cn(
              'glass overflow-hidden rounded-xl border transition-all duration-200',
              isSuccess
                ? 'border-emerald-500/20 hover:border-emerald-500/30'
                : 'border-destructive/20 hover:border-destructive/30',
              isFullscreen && 'fixed inset-4 z-50 rounded-2xl'
            )}
          >
            {/* Header */}
            <div
              className={cn(
                'flex items-center justify-between px-3 py-2.5 border-b',
                isSuccess ? 'border-emerald-500/10' : 'border-destructive/10'
              )}
            >
              <button onClick={onToggle} className="flex items-center gap-2.5 flex-1 text-left">
                {isSuccess ? (
                  <div
                    className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20"
                  >
                    <Check className="h-3 w-3 text-emerald-500" weight="bold" />
                  </div>
                ) : (
                  <div
                    className="flex h-5 w-5 items-center justify-center rounded-full bg-destructive/20"
                  >
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
                <span className="text-xs text-muted-foreground/50">
                  {getResultStats()}
                </span>
              </button>

              <div className="flex items-center gap-1">
                {/* View Mode Toggles */}
                {isExpanded && (
                  <div className="flex items-center gap-0.5 mr-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setViewMode('preview');
                      }}
                      className={cn(
                        'p-1.5 rounded-md transition-colors',
                        viewMode === 'preview'
                          ? 'bg-white/10 text-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-white/5'
                      )}
                      title="Preview view"
                    >
                      <TextT className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setViewMode('json');
                      }}
                      className={cn(
                        'p-1.5 rounded-md transition-colors',
                        viewMode === 'json'
                          ? 'bg-white/10 text-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-white/5'
                      )}
                      title="JSON view"
                    >
                      <FileJs className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setViewMode('raw');
                      }}
                      className={cn(
                        'p-1.5 rounded-md transition-colors',
                        viewMode === 'raw'
                          ? 'bg-white/10 text-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-white/5'
                      )}
                      title="Raw view"
                    >
                      <span className="text-[10px] font-mono">RAW</span>
                    </button>
                  </div>
                )}

                {/* Expand/Collapse */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsFullscreen(!isFullscreen);
                  }}
                  className="p-1.5 rounded-md text-muted-foreground hover:text-foreground
                             hover:bg-white/5 transition-colors"
                  title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
                >
                  {isFullscreen ? (
                    <ArrowsInSimple className="h-3.5 w-3.5" />
                  ) : (
                    <ArrowsOutSimple className="h-3.5 w-3.5" />
                  )}
                </button>

                <button
                  onClick={onToggle}
                  className="p-1.5 rounded-md text-muted-foreground hover:text-foreground
                             hover:bg-white/5 transition-colors"
                >
                  <motion.div
                    animate={{ rotate: isExpanded ? 180 : 0 }}
                    transition={{ duration: shouldReduceMotion ? 0 : 0.2 }}
                  >
                    <CaretDown className="h-4 w-4" />
                  </motion.div>
                </button>
              </div>
            </div>

            {/* Error Display */}
            {hasError && (
              <div className="px-3 py-2 bg-destructive/5 border-b border-destructive/10">
                <div className="flex items-start gap-2">
                  <Warning className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <div className="text-sm font-medium text-destructive">Error</div>
                    <div className="text-xs text-destructive/80 mt-0.5">{toolResult.error}</div>
                  </div>
                </div>
              </div>
            )}

            {/* Expanded Content */}
            <AnimatePresence initial={false}>
              {isExpanded && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{
                    duration: shouldReduceMotion ? 0 : 0.25,
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className="overflow-hidden"
                >
                  <div className="relative">
                    {/* Action Bar */}
                    <div
                      className="flex items-center justify-between px-3 py-2 border-b border-white/5
                                 bg-white/[0.02]"
                    >
                      <span className="text-xs text-muted-foreground">
                        {viewMode === 'json' ? 'JSON Format' : viewMode === 'raw' ? 'Raw Format' : 'Preview'}
                      </span>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={handleCopy}
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs
                                     text-muted-foreground hover:text-foreground hover:bg-white/5
                                     transition-colors"
                        >
                          {isCopied ? (
                            <>
                              <CheckCircle className="h-3 w-3 text-emerald-400" />
                              <span className="text-emerald-400">Copied!</span>
                            </>
                          ) : (
                            <>
                              <Copy className="h-3 w-3" />
                              Copy
                            </>
                          )}
                        </button>
                        <button
                          onClick={handleDownload}
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs
                                     text-muted-foreground hover:text-foreground hover:bg-white/5
                                     transition-colors"
                        >
                          <Download className="h-3 w-3" />
                          Download
                        </button>
                      </div>
                    </div>

                    {/* Content */}
                    <div
                      className={cn(
                        'bg-black/20',
                        isFullscreen && 'max-h-[calc(100vh-200px)] overflow-auto'
                      )}
                    >
                      {renderContent()}
                    </div>

                    {/* Large Result Warning */}
                    {isLarge && (
                      <div className="px-3 py-2 border-t border-white/5 bg-white/[0.02]">
                        <p className="text-xs text-muted-foreground">
                          Large result ({resultSize.toLocaleString()} characters).{' '}
                          {isTruncated && 'Click to expand full content.'}
                        </p>
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Preview when collapsed and large */}
            {!isExpanded && isLarge && (
              <div className="border-t border-white/5 px-3 py-2">
                <pre
                  className="overflow-x-auto text-xs text-muted-foreground
                             scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent"
                >
                  <code>{truncateText(displayContent, MAX_INLINE_LENGTH)}</code>
                </pre>
                <p className="mt-1 text-[10px] text-muted-foreground/50">
                  Click to expand ({resultSize.toLocaleString()} chars)
                </p>
              </div>
            )}
          </div>
        </div>
      </motion.div>

      {/* Fullscreen Overlay Backdrop */}
      {isFullscreen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40"
          onClick={() => setIsFullscreen(false)}
        />
      )}
    </>
  );
});
