import * as React from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'circular' | 'text' | 'rect';
  width?: number | string;
  height?: number | string;
  animated?: boolean;
}

function Skeleton({
  className,
  variant = 'default',
  width,
  height,
  animated = true,
  style,
  ...props
}: SkeletonProps) {
  const baseClasses = 'bg-white/5 relative overflow-hidden';

  const variantClasses = {
    default: 'rounded-md',
    circular: 'rounded-full',
    text: 'rounded h-4 w-24',
    rect: 'rounded-none',
  };

  return (
    <div
      className={cn(baseClasses, variantClasses[variant], className)}
      style={{
        width,
        height,
        ...style,
      }}
      {...props}
    >
      {animated && (
        <motion.div
          className="absolute inset-0 -translate-x-full shimmer"
          initial={{ x: '-100%' }}
          animate={{ x: '100%' }}
          transition={{
            repeat: Infinity,
            duration: 1.5,
            ease: 'linear',
          }}
        />
      )}
    </div>
  );
}

// Message skeleton for loading states
interface MessageSkeletonProps {
  isUser?: boolean;
  count?: number;
}

function MessageSkeleton({ isUser = false, count = 1 }: MessageSkeletonProps) {
  return (
    <div className={cn('flex gap-4', isUser ? 'flex-row-reverse' : 'flex-row')}>
      {/* Avatar */}
      <Skeleton variant="circular" width={32} height={32} className="shrink-0" />

      {/* Content */}
      <div className={cn('flex-1', isUser ? 'text-right' : 'text-left')}>
        <div className={cn('inline-block max-w-[85%] rounded-lg px-4 py-3 glass', isUser ? 'bg-primary/20' : '')}>
          <div className="space-y-2">
            <Skeleton width="100%" height={16} />
            <Skeleton width="80%" height={16} />
            {count > 1 && <Skeleton width="60%" height={16} />}
          </div>
        </div>
      </div>
    </div>
  );
}

// Tool call skeleton
function ToolCallSkeleton() {
  return (
    <div className="flex gap-3">
      <Skeleton variant="circular" width={28} height={28} className="shrink-0" />
      <div className="flex-1">
        <div className="glass rounded-lg p-3">
          <div className="flex items-center gap-2 mb-2">
            <Skeleton variant="circular" width={20} height={20} />
            <Skeleton width={100} height={16} />
          </div>
          <Skeleton width="100%" height={60} />
        </div>
      </div>
    </div>
  );
}

// Card skeleton for tool results or other cards
function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="glass rounded-lg p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Skeleton variant="circular" width={24} height={24} />
        <Skeleton width={120} height={18} />
      </div>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} width={i === lines - 1 ? '60%' : '100%'} height={14} />
      ))}
    </div>
  );
}

export { Skeleton, MessageSkeleton, ToolCallSkeleton, CardSkeleton };
