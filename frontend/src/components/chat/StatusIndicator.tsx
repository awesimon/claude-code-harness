import * as React from 'react';
import { Brain, Wrench } from '@phosphor-icons/react';
import type { ChatState } from '@/types';
import { cn } from '@/lib/utils';

interface StatusIndicatorProps {
  status: ChatState['status'];
}

export function StatusIndicator({ status }: StatusIndicatorProps) {
  const config = {
    idle: null,
    thinking: {
      icon: Brain,
      text: 'Thinking...',
      className: 'bg-muted border-border text-foreground',
    },
    tool_calling: {
      icon: Wrench,
      text: 'Using tools...',
      className: 'bg-muted border-border text-foreground',
    },
    streaming: null,
  };

  const current = config[status];

  if (!current) return null;

  const Icon = current.icon;

  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium',
        current.className
      )}
    >
      <Icon className="h-3.5 w-3.5 animate-pulse" weight="duotone" />
      <span>{current.text}</span>
    </div>
  );
}
