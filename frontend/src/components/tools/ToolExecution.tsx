import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  Wrench,
  CaretDown,
  FileText,
  Command,
  Globe,
  MagnifyingGlass,
  Terminal,
  Database,
  Code,
  FolderOpen,
  PencilSimple,
  Copy,
  CheckCircle,
  Spinner,
  Clock,
  Check,
  X,
  Warning,
  FileJs,
  TextT,
  Download,
  ArrowsOutSimple,
  ArrowsInSimple,
} from '@phosphor-icons/react';
import type { ToolCall as ToolCallType, ToolResult as ToolResultType } from '@/types';
import { cn, truncateText } from '@/lib/utils';

interface ToolExecutionProps {
  toolCall: ToolCallType;
  toolResult?: ToolResultType;
  isExpanded?: boolean;
  onToggle?: () => void;
  className?: string;
}

// Tool icon mapping
const toolIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  read_file: FileText,
  write_file: PencilSimple,
  edit_file: PencilSimple,
  file_search: MagnifyingGlass,
  glob: MagnifyingGlass,
  bash: Terminal,
  command: Command,
  exec: Terminal,
  grep: MagnifyingGlass,
  search: MagnifyingGlass,
  find: MagnifyingGlass,
  web_search: Globe,
  web_fetch: Globe,
  fetch: Globe,
  http: Globe,
  db_query: Database,
  database: Database,
  sql: Database,
  code: Code,
  analyze: Code,
  parse: Code,
  list_dir: FolderOpen,
  directory: FolderOpen,
  ls: FolderOpen,
};

const toolCategories: Record<string, string> = {
  read_file: 'file',
  write_file: 'file',
  edit_file: 'file',
  file_search: 'file',
  glob: 'file',
  bash: 'system',
  command: 'system',
  exec: 'system',
  grep: 'search',
  search: 'search',
  find: 'search',
  web_search: 'web',
  web_fetch: 'web',
  fetch: 'web',
  http: 'web',
  db_query: 'database',
  database: 'database',
  sql: 'database',
  code: 'code',
  analyze: 'code',
  parse: 'code',
  list_dir: 'directory',
  directory: 'directory',
  ls: 'directory',
};

const categoryColors: Record<string, string> = {
  file: 'text-blue-400 bg-blue-500/20 border-blue-500/20',
  system: 'text-purple-400 bg-purple-500/20 border-purple-500/20',
  search: 'text-amber-400 bg-amber-500/20 border-amber-500/20',
  web: 'text-cyan-400 bg-cyan-500/20 border-cyan-500/20',
  database: 'text-emerald-400 bg-emerald-500/20 border-emerald-500/20',
  code: 'text-pink-400 bg-pink-500/20 border-pink-500/20',
  directory: 'text-orange-400 bg-orange-500/20 border-orange-500/20',
  default: 'text-accent bg-accent/20 border-accent/20',
};

type ViewMode = 'preview' | 'json' | 'raw';

const MAX_PREVIEW_LENGTH = 500;
const MAX_INLINE_LENGTH = 200;

function highlightJson(json: string): string {
  return json
    .replace(/"(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"/g, '<span class="text-green-400">$&</span>')
    .replace(/\b(true|false|null)\b/g, '<span class="text-purple-400">$&</span>')
    .replace(/\b(\d+\.?\d*)\b/g, '<span class="text-amber-400">$&</span>')
    .replace(/[{\[}\]]/g, '<span class="text-cyan-400">$&</span>')
    .replace(/:/g, '<span class="text-muted-foreground">:</span>');
}

function parseParameters(args: Record<string, unknown>): { key: string; value: unknown; type: string }[] {
  return Object.entries(args).map(([key, value]) => ({
    key,
    value,
    type: typeof value,
  }));
}

function formatValue(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return 'undefined';
  if (typeof value === 'string') return value;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function getValuePreview(value: unknown, maxLength: number = 50): string {
  const formatted = formatValue(value);
  if (formatted.length <= maxLength) return formatted;
  return formatted.slice(0, maxLength) + '...';
}

export const ToolExecution = React.memo(function ToolExecution({
  toolCall,
  toolResult,
  isExpanded = false,
  onToggle,
  className,
}: ToolExecutionProps) {
  const shouldReduceMotion = useReducedMotion();
  const [viewMode, setViewMode] = React.useState<ViewMode>('preview');
  const [isCopied, setIsCopied] = React.useState(false);
  const [isFullscreen, setIsFullscreen] = React.useState(false);
  const [copiedParam, setCopiedParam] = React.useState<string | null>(null);

  const hasResult = !!toolResult;
  const isSuccess = toolResult?.success ?? false;
  const hasError = hasResult && !isSuccess && toolResult.error;

  const IconComponent = toolIcons[toolCall.name] || Wrench;
  const category = toolCategories[toolCall.name] || 'default';
  const colorClasses = categoryColors[category] || categoryColors.default;

  const parameters = React.useMemo(() => parseParameters(toolCall.arguments), [toolCall.arguments]);

  const formattedArgs = React.useMemo(() => {
    try {
      return JSON.stringify(toolCall.arguments, null, 2);
    } catch {
      return String(toolCall.arguments);
    }
  }, [toolCall.arguments]);

  const formattedResult = React.useMemo(() => {
    if (!toolResult) return '';
    try {
      if (typeof toolResult.result === 'string') return toolResult.result;
      return JSON.stringify(toolResult.result, null, 2);
    } catch {
      return String(toolResult.result);
    }
  }, [toolResult]);

  const jsonFormatted = React.useMemo(() => {
    if (!toolResult) return '';
    try {
      if (typeof toolResult.result === 'string') {
        const parsed = JSON.parse(toolResult.result);
        return JSON.stringify(parsed, null, 2);
      }
      return JSON.stringify(toolResult.result, null, 2);
    } catch {
      return formattedResult;
    }
  }, [toolResult, formattedResult]);

  const resultSize = formattedResult.length;
  const isLarge = resultSize > MAX_PREVIEW_LENGTH;
  const isTruncated = isLarge && !isExpanded;

  const displayContent = React.useMemo(() => {
    if (isTruncated) return truncateText(formattedResult, MAX_PREVIEW_LENGTH);
    return formattedResult;
  }, [formattedResult, isTruncated]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(formattedResult);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch {}
  };

  const handleCopyParam = async (key: string, value: unknown) => {
    try {
      await navigator.clipboard.writeText(formatValue(value));
      setCopiedParam(key);
      setTimeout(() => setCopiedParam(null), 2000);
    } catch {}
  };

  const handleDownload = () => {
    const blob = new Blob([formattedResult], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${toolCall.name}-result.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const getStatusIcon = () => {
    if (!hasResult) {
      return (
        <motion.div
          animate={shouldReduceMotion ? {} : { rotate: 360 }}
          transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
        >
          <Spinner className="h-4 w-4 text-accent" weight="bold" />
        </motion.div>
      );
    }
    if (isSuccess) {
      return <Check className="h-4 w-4 text-emerald-400" weight="bold" />;
    }
    return <X className="h-4 w-4 text-destructive" weight="bold" />;
  };

  const getResultStats = () => {
    if (!toolResult) return 'Executing...';
    if (typeof toolResult.result === 'object' && toolResult.result !== null) {
      const keys = Object.keys(toolResult.result as Record<string, unknown>);
      return `${keys.length} fields`;
    }
    if (typeof toolResult.result === 'string') {
      const lines = toolResult.result.split('\n').length;
      return `${lines} lines, ${resultSize} chars`;
    }
    return `${resultSize} chars`;
  };

  const renderResultContent = () => {
    if (!toolResult) return null;
    switch (viewMode) {
      case 'json':
        return (
          <pre
            className="overflow-x-auto p-3 text-xs font-mono leading-relaxed scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent"
            dangerouslySetInnerHTML={{ __html: highlightJson(jsonFormatted) }}
          />
        );
      case 'raw':
        return (
          <pre className="overflow-x-auto p-3 text-xs text-foreground/80 scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent">
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
                        ? JSON.stringify(value).slice(0, 100) + (JSON.stringify(value).length > 100 ? '...' : '')
                        : String(value).slice(0, 100) + (String(value).length > 100 ? '...' : '')}
                    </span>
                  </div>
                ))}
              {!isExpanded && Object.keys(toolResult.result as Record<string, unknown>).length > 5 && (
                <div className="text-xs text-muted-foreground">
                  +{Object.keys(toolResult.result as Record<string, unknown>).length - 5} more fields...
                </div>
              )}
            </div>
          );
        }
        return (
          <pre className="overflow-x-auto p-3 text-xs text-foreground/80 scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent">
            <code>{displayContent}</code>
          </pre>
        );
    }
  };

  return (
    <>
      <motion.div
        initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
        className={cn('flex gap-3', className)}
      >
        {/* Tool Icon Avatar */}
        <div
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-full glass-strong border',
            colorClasses
          )}
        >
          <IconComponent className="h-4 w-4" />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div
            className={cn(
              'glass overflow-hidden rounded-xl border transition-all duration-200',
              'hover:shadow-lg hover:shadow-accent/5',
              colorClasses.split(' ').slice(1).join(' '),
              isFullscreen && 'fixed inset-4 z-50 rounded-2xl'
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2.5">
              <button onClick={onToggle} className="flex items-center gap-2.5 flex-1 text-left">
                <span className={cn('text-sm font-medium', colorClasses.split(' ')[0])}>
                  {toolCall.name}
                </span>
                <span className="text-xs text-muted-foreground">
                  {hasResult ? (isSuccess ? 'Success' : 'Error') : 'Executing...'}
                </span>
                {hasResult && (
                  <span className="text-xs text-muted-foreground/50">{getResultStats()}</span>
                )}
              </button>

              <div className="flex items-center gap-1">
                {getStatusIcon()}

                {/* View Mode Toggles (only when expanded and has result) */}
                {isExpanded && hasResult && (
                  <div className="flex items-center gap-0.5 ml-2">
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

                {/* Fullscreen */}
                {hasResult && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setIsFullscreen(!isFullscreen);
                    }}
                    className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-white/5 transition-colors ml-1"
                    title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
                  >
                    {isFullscreen ? (
                      <ArrowsInSimple className="h-3.5 w-3.5" />
                    ) : (
                      <ArrowsOutSimple className="h-3.5 w-3.5" />
                    )}
                  </button>
                )}

                {/* Expand/Collapse */}
                <button
                  onClick={onToggle}
                  className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-white/5 transition-colors"
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

            {/* Collapsed Preview */}
            {!isExpanded && (
              <div className="border-t border-white/5 px-3 py-2">
                <div className="flex flex-wrap gap-2">
                  {parameters.slice(0, 3).map((param) => (
                    <span
                      key={param.key}
                      className="inline-flex items-center gap-1 rounded-md bg-white/5 px-2 py-1 text-xs"
                      title={`${param.key}: ${formatValue(param.value)}`}
                    >
                      <span className="text-muted-foreground">{param.key}=</span>
                      <span className="text-foreground/80 truncate max-w-[100px]">
                        {getValuePreview(param.value, 30)}
                      </span>
                    </span>
                  ))}
                  {parameters.length > 3 && (
                    <span className="text-xs text-muted-foreground">+{parameters.length - 3} more</span>
                  )}
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
                  transition={{ duration: shouldReduceMotion ? 0 : 0.2, ease: [0.16, 1, 0.3, 1] }}
                  className="overflow-hidden"
                >
                  <div className="border-t border-white/5">
                    {/* Tool Call Section */}
                    <div className="px-3 py-3">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs text-muted-foreground">Tool Call Parameters</span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            navigator.clipboard.writeText(formattedArgs);
                          }}
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-white/5 transition-colors"
                        >
                          <Copy className="h-3 w-3" />
                          Copy JSON
                        </button>
                      </div>

                      {/* Parameters Grid */}
                      <div className="space-y-2">
                        {parameters.map((param) => (
                          <div
                            key={param.key}
                            className="group flex items-start gap-2 rounded-lg bg-black/20 p-2 transition-colors hover:bg-black/30"
                          >
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-medium text-muted-foreground">{param.key}</span>
                                <span className="text-[10px] text-muted-foreground/50 uppercase">{param.type}</span>
                              </div>
                              <div className="mt-0.5 text-sm text-foreground/90 break-all font-mono">
                                {formatValue(param.value)}
                              </div>
                            </div>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleCopyParam(param.key, param.value);
                              }}
                              className="opacity-0 group-hover:opacity-100 transition-opacity rounded-md p-1 hover:bg-white/10"
                              title="Copy value"
                            >
                              {copiedParam === param.key ? (
                                <CheckCircle className="h-3 w-3 text-emerald-400" weight="fill" />
                              ) : (
                                <Copy className="h-3 w-3 text-muted-foreground" />
                              )}
                            </button>
                          </div>
                        ))}
                      </div>

                      {/* Raw JSON */}
                      <div className="mt-3">
                        <div className="text-xs text-muted-foreground mb-1">Raw JSON</div>
                        <pre className="overflow-x-auto rounded-lg bg-black/30 p-3 text-xs text-foreground/80 scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent">
                          <code>{formattedArgs}</code>
                        </pre>
                      </div>
                    </div>

                    {/* Tool Result Section (if available) */}
                    {hasResult && (
                      <div className="border-t border-white/5 px-3 py-3">
                        {/* Error Display */}
                        {hasError && (
                          <div className="mb-3 px-3 py-2 bg-destructive/5 border border-destructive/20 rounded-lg">
                            <div className="flex items-start gap-2">
                              <Warning className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
                              <div className="flex-1">
                                <div className="text-sm font-medium text-destructive">Error</div>
                                <div className="text-xs text-destructive/80 mt-0.5">{toolResult.error}</div>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Result Header */}
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-xs text-muted-foreground">
                            Result ({viewMode === 'json' ? 'JSON' : viewMode === 'raw' ? 'Raw' : 'Preview'})
                          </span>
                          <div className="flex items-center gap-1">
                            <button
                              onClick={handleCopy}
                              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-white/5 transition-colors"
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
                              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-white/5 transition-colors"
                            >
                              <Download className="h-3 w-3" />
                              Download
                            </button>
                          </div>
                        </div>

                        {/* Result Content */}
                        <div
                          className={cn(
                            'bg-black/20 rounded-lg',
                            isFullscreen && 'max-h-[calc(100vh-400px)] overflow-auto'
                          )}
                        >
                          {renderResultContent()}
                        </div>

                        {/* Large Result Warning */}
                        {isLarge && (
                          <div className="mt-2 px-3 py-2 border-t border-white/5 bg-white/[0.02] rounded-b-lg">
                            <p className="text-xs text-muted-foreground">
                              Large result ({resultSize.toLocaleString()} characters)
                            </p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
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

ToolExecution.displayName = 'ToolExecution';
