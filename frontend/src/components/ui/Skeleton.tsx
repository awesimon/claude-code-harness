import * as React from 'react';
import { cn } from '@/lib/utils';

interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'circular' | 'text' | 'rect';
  width?: number | string;
  height?: number | string;
  animated?: boolean;
}

/**
 * Skeleton - 基础骨架屏组件
 *
 * 用于显示内容加载中的占位状态
 */
function Skeleton({
  className,
  variant = 'default',
  width,
  height,
  animated = true,
  style,
  ...props
}: SkeletonProps) {
  const baseClasses = 'bg-muted relative overflow-hidden';

  const variantClasses = {
    default: 'rounded-md',
    circular: 'rounded-full',
    text: 'rounded h-4 w-24',
    rect: 'rounded-none',
  };

  return (
    <div
      className={cn(baseClasses, variantClasses[variant], animated && 'skeleton-pulse', className)}
      style={{
        width,
        height,
        ...style,
      }}
      {...props}
    />
  );
}

/**
 * SkeletonText - 文本骨架屏
 */
interface SkeletonTextProps {
  lines?: number;
  className?: string;
  lineClassName?: string;
  lastLineWidth?: string;
  animated?: boolean;
}

function SkeletonText({
  lines = 3,
  className,
  lineClassName,
  lastLineWidth = '60%',
  animated = true,
}: SkeletonTextProps) {
  return (
    <div className={cn('space-y-2', className)} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          variant="text"
          animated={animated}
          className={cn(
            'h-4 w-full',
            i === lines - 1 && lastLineWidth !== '100%' ? lastLineWidth : '',
            lineClassName
          )}
        />
      ))}
    </div>
  );
}

/**
 * MessageSkeleton - 消息骨架屏
 *
 * 用于聊天消息加载状态
 */
interface MessageSkeletonProps {
  isUser?: boolean;
  count?: number;
  animated?: boolean;
}

function MessageSkeleton({ isUser = false, count = 1, animated = true }: MessageSkeletonProps) {
  return (
    <div className={cn('flex gap-4', isUser ? 'flex-row-reverse' : 'flex-row')}>
      {/* Avatar */}
      <Skeleton
        variant="circular"
        width={32}
        height={32}
        animated={animated}
        className="shrink-0"
      />

      {/* Content */}
      <div className={cn('flex-1', isUser ? 'text-right' : 'text-left')}>
        <div
          className={cn(
            'inline-block max-w-[85%] rounded-lg px-4 py-3 bg-muted',
            isUser ? 'bg-muted' : ''
          )}
        >
          <div className="space-y-2">
            <Skeleton width="100%" height={16} animated={animated} />
            <Skeleton width="80%" height={16} animated={animated} />
            {count > 1 && <Skeleton width="60%" height={16} animated={animated} />}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * ToolCallSkeleton - 工具调用骨架屏
 *
 * 用于工具调用加载状态
 */
interface ToolCallSkeletonProps {
  showInput?: boolean;
  animated?: boolean;
}

function ToolCallSkeleton({ showInput = true, animated = true }: ToolCallSkeletonProps) {
  return (
    <div className="flex gap-3">
      <Skeleton
        variant="circular"
        width={28}
        height={28}
        animated={animated}
        className="shrink-0"
      />
      <div className="flex-1">
        <div className="border rounded-lg p-3 bg-muted">
          <div className="flex items-center gap-2 mb-2">
            <Skeleton
              variant="circular"
              width={20}
              height={20}
              animated={animated}
            />
            <Skeleton width={100} height={16} animated={animated} />
          </div>
          {showInput && (
            <Skeleton width="100%" height={60} animated={animated} />
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * CardSkeleton - 卡片骨架屏
 *
 * 用于卡片内容加载状态
 */
interface CardSkeletonProps {
  lines?: number;
  hasImage?: boolean;
  hasFooter?: boolean;
  animated?: boolean;
}

function CardSkeleton({
  lines = 3,
  hasImage = false,
  hasFooter = false,
  animated = true,
}: CardSkeletonProps) {
  return (
    <div className="border rounded-lg p-4 space-y-3 bg-background">
      {/* Image */}
      {hasImage && (
        <Skeleton
          className="w-full h-40 rounded-lg"
          animated={animated}
        />
      )}

      <div className="flex items-center gap-2">
        <Skeleton
          variant="circular"
          width={24}
          height={24}
          animated={animated}
        />
        <Skeleton width={120} height={18} animated={animated} />
      </div>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          width={i === lines - 1 ? '60%' : '100%'}
          height={14}
          animated={animated}
        />
      ))}

      {/* Footer */}
      {hasFooter && (
        <div className="pt-2 flex items-center justify-between">
          <Skeleton width={80} height={32} animated={animated} />
          <div className="flex gap-2">
            <Skeleton width={32} height={32} variant="circular" animated={animated} />
            <Skeleton width={32} height={32} variant="circular" animated={animated} />
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * ChatListSkeleton - 聊天列表骨架屏
 *
 * 用于聊天列表加载状态
 */
interface ChatListSkeletonProps {
  count?: number;
  animated?: boolean;
}

function ChatListSkeleton({ count = 5, animated = true }: ChatListSkeletonProps) {
  return (
    <div className="space-y-1">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 p-3 rounded-lg">
          <Skeleton
            variant="circular"
            width={36}
            height={36}
            animated={animated}
          />
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center justify-between">
              <Skeleton width="50%" height={16} animated={animated} />
              <Skeleton width={35} height={12} animated={animated} />
            </div>
            <Skeleton width="80%" height={14} animated={animated} />
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * InputSkeleton - 输入框骨架屏
 *
 * 用于输入区域加载状态
 */
interface InputSkeletonProps {
  showActions?: boolean;
  animated?: boolean;
}

function InputSkeleton({ showActions = true, animated = true }: InputSkeletonProps) {
  return (
    <div className="rounded-lg border bg-background p-3 space-y-3">
      {/* Text area */}
      <Skeleton className="w-full h-20" animated={animated} />

      {/* Actions */}
      {showActions && (
        <div className="flex items-center justify-between">
          <div className="flex gap-2">
            <Skeleton width={32} height={32} variant="circular" animated={animated} />
            <Skeleton width={32} height={32} variant="circular" animated={animated} />
            <Skeleton width={32} height={32} variant="circular" animated={animated} />
          </div>
          <Skeleton width={80} height={36} animated={animated} className="rounded-md" />
        </div>
      )}
    </div>
  );
}

export {
  Skeleton,
  SkeletonText,
  MessageSkeleton,
  ToolCallSkeleton,
  CardSkeleton,
  ChatListSkeleton,
  InputSkeleton,
};

export type {
  SkeletonProps,
  SkeletonTextProps,
  MessageSkeletonProps,
  ToolCallSkeletonProps,
  CardSkeletonProps,
  ChatListSkeletonProps,
  InputSkeletonProps,
};
