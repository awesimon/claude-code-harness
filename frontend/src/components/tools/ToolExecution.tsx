import * as React from 'react';
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
  file: 'text-foreground bg-muted',
  system: 'text-foreground bg-muted',
  search: 'text-foreground bg-muted',
  web: 'text-foreground bg-muted',
  database: 'text-foreground bg-muted',
  code: 'text-foreground bg-muted',
  directory: 'text-foreground bg-muted',
  default: 'text-foreground bg-muted',
};

type ViewMode = 'preview' | 'json' | 'raw';

const MAX_PREVIEW_LENGTH = 500;
const MAX_INLINE_LENGTH = 200;

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
      return <Spinner className="h-4 w-4 text-muted-foreground animate-spin" weight="bold" />;
    }
    if (isSuccess) {
      return <Check className="h-4 w-4 text-foreground" weight="bold" />;
    }
    return <X className="h-4 w-4 text-foreground" weight="bold" />;
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
          <pre className="overflow-x-auto p-3 text-xs font-mono leading-relaxed scrollbar-thin">
            <code>{jsonFormatted}</code>
          </pre>
        );
      case 'raw':
        return (
          <pre className="overflow-x-auto p-3 text-xs text-foreground scrollbar-thin">
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
                    <span className="text-foreground break-all">
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
          <pre className="overflow-x-auto p-3 text-xs text-foreground scrollbar-thin">
            <code>{displayContent}</code>
          </pre>
        );
    }
  };

  return (
    <>
      <div className={cn('flex gap-3', className)}>
        {/* Tool Icon Avatar */}
        <div
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-full border',
            colorClasses
          )}
        >
          <IconComponent className="h-4 w-4" />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div
            className={cn(
              'overflow-hidden rounded-lg border border-border transition-all duration-200',
              'bg-muted',
              isFullscreen && 'fixed inset-4 z-50 rounded-xl'
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2.5 bg-background">
              <button onClick={onToggle} className="flex items-center gap-2.5 flex-1 text-left">
                <span className="text-sm font-medium text-foreground">
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
                          ? 'bg-muted text-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted'
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
                          ? 'bg-muted text-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted'
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
                          ? 'bg-muted text-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted'
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
                    className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors ml-1"
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
                  className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  <CaretDown
                    className={cn(
                      'h-4 w-4 transition-transform duration-200',
                      isExpanded && 'rotate-180'
                    )}
                  />
                </button>
              </div>
            </div>

            {/* Collapsed Preview */}
            {!isExpanded && (
              <div className="border-t border-border px-3 py-2">
                <div className="flex flex-wrap gap-2">
                  {parameters.slice(0, 3).map((param) => (
                    <span
                      key={param.key}
                      className="inline-flex items-center gap-1 rounded-md bg-background px-2 py-1 text-xs"
                      title={`${param.key}: ${formatValue(param.value)}`}
                    >
                      <span className="text-muted-foreground">{param.key}=</span>
                      <span className="text-foreground truncate max-w-[100px]">
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
            {isExpanded && (
              <div className="overflow-hidden border-t border-border">
                {/* Tool Call Section */}
                <div className="px-3 py-3 bg-background">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-muted-foreground">Tool Call Parameters</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        navigator.clipboard.writeText(formattedArgs);
                      }}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
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
                        className="group flex items-start gap-2 rounded-lg bg-muted p-2 transition-colors hover:bg-muted/80"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-medium text-muted-foreground">{param.key}</span>
                            <span className="text-[10px] text-muted-foreground/50 uppercase">{param.type}</span>
                          </div>
                          <div className="mt-0.5 text-sm text-foreground break-all font-mono">
                            {formatValue(param.value)}
                          </div>
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCopyParam(param.key, param.value);
                          }}
                          className="opacity-0 group-hover:opacity-100 transition-opacity rounded-md p-1 hover:bg-background"
                          title="Copy value"
                        >
                          {copiedParam === param.key ? (
                            <CheckCircle className="h-3 w-3 text-foreground" weight="fill" />
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
                    <pre className="overflow-x-auto rounded-lg bg-muted p-3 text-xs text-foreground scrollbar-thin">
                      <code>{formattedArgs}</code>
                    </pre>
                  </div>
                </div>

                {/* Tool Result Section (if available) */}
                {hasResult && (
                  <div className="border-t border-border px-3 py-3 bg-background">
                    {/* Error Display */}
                    {hasError && (
                      <div className="mb-3 px-3 py-2 bg-muted border border-border rounded-lg">
                        <div className="flex items-start gap-2">
                          <Warning className="h-4 w-4 text-foreground shrink-0 mt-0.5" />
                          <div className="flex-1">
                            <div className="text-sm font-medium text-foreground">Error</div>
                            <div className="text-xs text-muted-foreground mt-0.5">{toolResult.error}</div>
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
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                        >
                          {isCopied ? (
                            <>
                              <CheckCircle className="h-3 w-3 text-foreground" />
                              <span className="text-foreground">Copied!</span>
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
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                        >
                          <Download className="h-3 w-3" />
                          Download
                        </button>
                      </div>
                    </div>

                    {/* Result Content */}
                    <div
                      className={cn(
                        'bg-muted rounded-lg',
                        isFullscreen && 'max-h-[calc(100vh-400px)] overflow-auto'
                      )}
                    >
                      {renderResultContent()}
                    </div>

                    {/* Large Result Warning */}
                    {isLarge && (
                      <div className="mt-2 px-3 py-2 border-t border-border bg-muted/50 rounded-b-lg">
                        <p className="text-xs text-muted-foreground">
                          Large result ({resultSize.toLocaleString()} characters)
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Fullscreen Overlay Backdrop */}
      {isFullscreen && (
        <div
          className="fixed inset-0 bg-background/80 z-40"
          onClick={() => setIsFullscreen(false)}
        />
      )}
    </>
  );
});

ToolExecution.displayName = 'ToolExecution';
