import * as React from 'react';
import {
  motion,
  AnimatePresence,
  useReducedMotion,
} from 'framer-motion';
import { cn } from '@/lib/utils';

type TooltipPosition = 'top' | 'bottom' | 'left' | 'right';

interface TooltipProps {
  /** 触发元素 */
  children: React.ReactElement;
  /** 提示内容 */
  content: React.ReactNode;
  /** 提示位置 */
  position?: TooltipPosition;
  /** 延迟显示时间（毫秒） */
  delay?: number;
  /** 是否禁用 */
  disabled?: boolean;
  /** 自定义类名 */
  className?: string;
  /** 内容类名 */
  contentClassName?: string;
  /** 偏移量 */
  offset?: number;
}

/**
 * Tooltip - 提示组件
 *
 * 鼠标悬停时显示提示信息
 */
export function Tooltip({
  children,
  content,
  position = 'top',
  delay = 200,
  disabled = false,
  className,
  contentClassName,
  offset = 8,
}: TooltipProps) {
  const [isVisible, setIsVisible] = React.useState(false);
  const [mounted, setMounted] = React.useState(false);
  const triggerRef = React.useRef<HTMLElement>(null);
  const timeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const shouldReduceMotion = useReducedMotion();

  React.useEffect(() => {
    setMounted(true);
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const showTooltip = () => {
    if (disabled) return;
    timeoutRef.current = setTimeout(() => {
      setIsVisible(true);
    }, delay);
  };

  const hideTooltip = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    setIsVisible(false);
  };

  const positionClasses = {
    top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
    bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
    left: 'right-full top-1/2 -translate-y-1/2 mr-2',
    right: 'left-full top-1/2 -translate-y-1/2 ml-2',
  };

  const arrowClasses = {
    top: 'top-full left-1/2 -translate-x-1/2 -mt-1 border-l-transparent border-r-transparent border-b-transparent',
    bottom: 'bottom-full left-1/2 -translate-x-1/2 -mb-1 border-l-transparent border-r-transparent border-t-transparent',
    left: 'left-full top-1/2 -translate-y-1/2 -ml-1 border-t-transparent border-b-transparent border-r-transparent',
    right: 'right-full top-1/2 -translate-y-1/2 -mr-1 border-t-transparent border-b-transparent border-l-transparent',
  };

  const arrowBorderColors = {
    top: 'border-t-popover',
    bottom: 'border-b-popover',
    left: 'border-l-popover',
    right: 'border-r-popover',
  };

  const child = React.cloneElement(children, {
    ref: triggerRef,
    onMouseEnter: showTooltip,
    onMouseLeave: hideTooltip,
    onFocus: showTooltip,
    onBlur: hideTooltip,
  });

  return (
    <span className={cn('relative inline-flex', className)}>
      {child}
      {mounted && (
        <AnimatePresence>
          {isVisible && (
            <motion.div
              initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
              className={cn(
                'absolute z-50 whitespace-nowrap rounded-md bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md ring-1 ring-border',
                positionClasses[position],
                contentClassName
              )}
              role="tooltip"
            >
              {content}
              {/* Arrow */}
              <span
                className={cn(
                  'absolute h-0 w-0 border-4',
                  arrowClasses[position],
                  arrowBorderColors[position]
                )}
              />
            </motion.div>
          )}
        </AnimatePresence>
      )}
    </span>
  );
}

/**
 * TooltipProvider - 提示上下文提供者
 *
 * 用于统一管理提示配置
 */
interface TooltipProviderProps {
  children: React.ReactNode;
  delay?: number;
}

const TooltipContext = React.createContext<{ delay: number }>({ delay: 200 });

export function TooltipProvider({ children, delay = 200 }: TooltipProviderProps) {
  return (
    <TooltipContext.Provider value={{ delay }}>
      {children}
    </TooltipContext.Provider>
  );
}

/**
 * IconTooltip - 图标提示组件
 *
 * 带有图标的提示，常用于帮助说明
 */
interface IconTooltipProps extends Omit<TooltipProps, 'children'> {
  icon: React.ReactNode;
  iconClassName?: string;
}

export function IconTooltip({
  icon,
  iconClassName,
  ...props
}: IconTooltipProps) {
  return (
    <Tooltip {...props}>
      <button
        type="button"
        className={cn(
          'inline-flex items-center justify-center rounded-full p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus:outline-none focus:ring-2 focus:ring-ring',
          iconClassName
        )}
        aria-label="帮助信息"
      >
        {icon}
      </button>
    </Tooltip>
  );
}

/**
 * ActionTooltip - 操作提示组件
 *
 * 用于显示操作按钮的功能说明，通常带有快捷键
 */
interface ActionTooltipProps extends Omit<TooltipProps, 'content'> {
  action: string;
  shortcut?: string;
}

export function ActionTooltip({
  action,
  shortcut,
  ...props
}: ActionTooltipProps) {
  return (
    <Tooltip
      content={
        <span className="flex items-center gap-2">
          {action}
          {shortcut && (
            <kbd className="ml-1 rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
              {shortcut}
            </kbd>
          )}
        </span>
      }
      {...props}
    />
  );
}

export type { TooltipPosition, TooltipProps, IconTooltipProps, ActionTooltipProps };
