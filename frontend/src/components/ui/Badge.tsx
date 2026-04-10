import * as React from 'react';
import { cn } from '@/lib/utils';

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** 徽章变体 */
  variant?: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning';
  /** 徽章大小 */
  size?: 'sm' | 'default' | 'lg';
  /** 是否可移除 */
  onRemove?: () => void;
  /** 是否为点状徽章 */
  dot?: boolean;
}

/**
 * Badge - 徽章/标签组件
 *
 * 用于显示状态、标签、计数或分类信息
 */
const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  (
    {
      className,
      variant = 'default',
      size = 'default',
      onRemove,
      dot = false,
      children,
      ...props
    },
    ref
  ) => {
    const variantClasses = {
      default: 'bg-foreground text-background',
      secondary: 'bg-muted text-foreground',
      destructive: 'bg-foreground text-background',
      outline: 'border border-border bg-background text-foreground',
      success: 'bg-muted text-foreground',
      warning: 'bg-muted text-foreground',
    };

    const sizeClasses = {
      sm: 'text-xs px-2 py-0.5',
      default: 'text-xs px-2.5 py-0.5',
      lg: 'text-sm px-3 py-1',
    };

    return (
      <span
        ref={ref}
        className={cn(
          'inline-flex items-center gap-1 rounded-full font-medium transition-colors',
          variantClasses[variant],
          sizeClasses[size],
          onRemove && 'pr-1',
          className
        )}
        {...props}
      >
        {dot && (
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              variant === 'default' && 'bg-background',
              variant === 'secondary' && 'bg-foreground',
              variant === 'destructive' && 'bg-background',
              variant === 'outline' && 'bg-foreground',
              variant === 'success' && 'bg-foreground',
              variant === 'warning' && 'bg-foreground'
            )}
          />
        )}
        {children}
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="ml-1 rounded-full p-0.5 hover:bg-muted transition-colors"
            aria-label="移除标签"
          >
            <svg
              className="h-3 w-3"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        )}
      </span>
    );
  }
);

Badge.displayName = 'Badge';

/**
 * StatusBadge - 状态徽章组件
 *
 * 用于显示状态信息，如在线、离线、忙碌等
 */
interface StatusBadgeProps extends Omit<BadgeProps, 'variant' | 'dot'> {
  status: 'online' | 'offline' | 'busy' | 'away' | 'default';
}

function StatusBadge({ status, className, ...props }: StatusBadgeProps) {
  const statusConfig = {
    online: { variant: 'default' as const, label: '在线' },
    offline: { variant: 'secondary' as const, label: '离线' },
    busy: { variant: 'default' as const, label: '忙碌' },
    away: { variant: 'secondary' as const, label: '离开' },
    default: { variant: 'default' as const, label: '默认' },
  };

  const config = statusConfig[status];

  return (
    <Badge
      variant={config.variant}
      dot
      className={className}
      {...props}
    >
      {config.label}
    </Badge>
  );
}

/**
 * CountBadge - 计数徽章组件
 *
 * 用于显示计数，如消息数量、通知数量等
 */
interface CountBadgeProps extends Omit<BadgeProps, 'children'> {
  count: number;
  max?: number;
  showZero?: boolean;
}

function CountBadge({
  count,
  max = 99,
  showZero = false,
  variant = 'default',
  size = 'sm',
  className,
  ...props
}: CountBadgeProps) {
  if (count === 0 && !showZero) return null;

  const displayCount = count > max ? `${max}+` : count;

  return (
    <Badge
      variant={variant}
      size={size}
      className={cn('min-w-[1.25rem] justify-center', className)}
      {...props}
    >
      {displayCount}
    </Badge>
  );
}

/**
 * Tag - 标签组件
 *
 * 用于显示可交互的标签
 */
interface TagProps extends BadgeProps {
  selected?: boolean;
  disabled?: boolean;
}

const Tag = React.forwardRef<HTMLSpanElement, TagProps>(
  ({ selected, disabled, className, ...props }, ref) => {
    return (
      <Badge
        ref={ref}
        variant={selected ? 'default' : 'outline'}
        className={cn(
          'cursor-pointer transition-colors',
          disabled && 'cursor-not-allowed opacity-50',
          className
        )}
        {...props}
      />
    );
  }
);

Tag.displayName = 'Tag';

export { Badge, StatusBadge, CountBadge, Tag };
export type { BadgeProps, StatusBadgeProps, CountBadgeProps, TagProps };
