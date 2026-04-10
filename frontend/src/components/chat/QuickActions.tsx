import * as React from 'react';
import { FolderOpen, MagnifyingGlass, FileText, Terminal } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

interface QuickAction {
  icon: React.ReactNode;
  title: string;
  description: string;
  command: string;
}

const quickActions: QuickAction[] = [
  {
    icon: <FolderOpen className="h-5 w-5" weight="duotone" />,
    title: 'Explore Files',
    description: 'View directory structure with glob',
    command: 'Help me explore the project file structure',
  },
  {
    icon: <MagnifyingGlass className="h-5 w-5" weight="duotone" />,
    title: 'Search Code',
    description: 'Find code patterns with grep',
    command: 'Search for TODO or FIXME comments in the codebase',
  },
  {
    icon: <FileText className="h-5 w-5" weight="duotone" />,
    title: 'Read Files',
    description: 'Open and analyze any file',
    command: 'Read the main configuration file and explain it',
  },
  {
    icon: <Terminal className="h-5 w-5" weight="duotone" />,
    title: 'Run Commands',
    description: 'Execute bash commands safely',
    command: 'Show me the current git status',
  },
];

interface QuickActionsProps {
  onSelect: (command: string) => void;
}

export function QuickActions({ onSelect }: QuickActionsProps) {
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-center gap-2 text-muted-foreground">
        <span className="text-xs font-medium uppercase tracking-wider">Quick Actions</span>
      </div>

      {/* Actions Grid */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {quickActions.map((action) => (
          <button
            key={action.title}
            onClick={() => onSelect(action.command)}
            className={cn(
              'group relative overflow-hidden rounded-lg border border-border p-4 text-left transition-all duration-200',
              'bg-background hover:border-foreground/50 hover:bg-muted',
              'active:scale-[0.98]'
            )}
          >
            <div className="relative">
              <div className="mb-3 flex items-center gap-2">
                <div className="rounded-lg bg-muted p-2 text-foreground">
                  {action.icon}
                </div>
              </div>
              <h3 className="mb-1 text-sm font-medium text-foreground">{action.title}</h3>
              <p className="text-xs text-muted-foreground">{action.description}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
