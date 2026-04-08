import * as React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
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
  /** 是否显示脉冲动画 */
  pulse?: boolean;
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
      pulse = false,
      children,
      ...props
    },
    ref
  ) => {
    const shouldReduceMotion = useReducedMotion();

    const variantClasses = {
      default: 'bg-primary text-primary-foreground hover:bg-primary/90',
      secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/90',
      destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
      outline: 'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
      success: 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/20',
      warning: 'bg-amber-500/10 text-amber-600 border border-amber-500/20',
    };

    const sizeClasses = {
      sm: 'text-xs px-2 py-0.5',
      default: 'text-xs px-2.5 py-0.5',
      lg: 'text-sm px-3 py-1',
    };

    const pulseAnimation = pulse && !shouldReduceMotion && {
      animate: { scale: [1, 1.05, 1] },
      transition: { duration: 2, repeat: Infinity, ease: 'easeInOut' },
    };

    return (
      <span
        ref={ref}
        className={cn(
          'inline-flex items-center gap-1 rounded-full font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
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
              variant === 'default' && 'bg-primary-foreground',
              variant === 'secondary' && 'bg-secondary-foreground',
              variant === 'destructive' && 'bg-destructive-foreground',
              variant === 'outline' && 'bg-foreground',
              variant === 'success' && 'bg-emerald-600',
              variant === 'warning' && 'bg-amber-600'
            )}
          />
        )}
        {children}
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="ml-1 rounded-full p-0.5 hover:bg-black/10 dark:hover:bg-white/10 transition-colors"
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
    online: { variant: 'success' as const, label: '在线' },
    offline: { variant: 'secondary' as const, label: '离线' },
    busy: { variant: 'destructive' as const, label: '忙碌' },
    away: { variant: 'warning' as const, label: '离开' },
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
  variant = 'destructive',
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
          'cursor-pointer transition-all',
          disabled && 'cursor-not-allowed opacity-50',
          selected && 'shadow-sm',
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
