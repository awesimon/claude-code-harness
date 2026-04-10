import * as React from 'react';
import { X, Warning, CheckCircle, Info } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

export interface AlertProps {
  variant?: 'default' | 'destructive' | 'success' | 'warning';
  title?: string;
  children: React.ReactNode;
  onDismiss?: () => void;
  className?: string;
  /** 是否显示图标 */
  showIcon?: boolean;
  /** 自动关闭时间（毫秒），0 表示不自动关闭 */
  autoClose?: number;
}

const variantConfig = {
  default: {
    container: 'bg-muted border-border text-foreground',
    icon: Info,
    iconClass: 'text-foreground',
  },
  destructive: {
    container: 'bg-muted border-border text-foreground',
    icon: Warning,
    iconClass: 'text-foreground',
  },
  success: {
    container: 'bg-muted border-border text-foreground',
    icon: CheckCircle,
    iconClass: 'text-foreground',
  },
  warning: {
    container: 'bg-muted border-border text-foreground',
    icon: Warning,
    iconClass: 'text-foreground',
  },
};

/**
 * Alert - 警告提示组件
 *
 * 用于显示重要信息、警告、成功或错误状态
 */
export function Alert({
  variant = 'default',
  title,
  children,
  onDismiss,
  className,
  showIcon = true,
  autoClose = 0,
}: AlertProps) {
  const config = variantConfig[variant];
  const Icon = config.icon;
  const [isVisible, setIsVisible] = React.useState(true);

  // 自动关闭逻辑
  React.useEffect(() => {
    if (autoClose > 0) {
      const timer = setTimeout(() => {
        setIsVisible(false);
        setTimeout(() => {
          onDismiss?.();
        }, 200);
      }, autoClose);
      return () => clearTimeout(timer);
    }
  }, [autoClose, onDismiss]);

  if (!isVisible) return null;

  return (
    <div
      className={cn(
        'relative rounded-lg border p-4',
        config.container,
        className
      )}
      role="alert"
    >
      <div className="flex gap-3">
        {showIcon && (
          <Icon className={cn('h-5 w-5 shrink-0 mt-0.5', config.iconClass)} weight="fill" />
        )}
        <div className={cn('flex-1 min-w-0', !showIcon && 'pl-0')}>
          {title && (
            <h5 className="mb-1 font-medium leading-none tracking-tight">{title}</h5>
          )}
          <div className="text-sm opacity-90">{children}</div>
        </div>
        {onDismiss && (
          <button
            onClick={() => {
              setIsVisible(false);
              setTimeout(() => onDismiss(), 200);
            }}
            className="shrink-0 rounded-md p-1 opacity-70 transition-opacity hover:opacity-100 hover:bg-muted"
            aria-label="关闭提示"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* 自动关闭进度条 */}
      {autoClose > 0 && (
        <div
          className="absolute bottom-0 left-0 h-0.5 bg-current opacity-30"
          style={{
            animation: `shrink ${autoClose}ms linear forwards`,
          }}
        />
      )}

      <style>{`
        @keyframes shrink {
          from { width: 100%; }
          to { width: 0%; }
        }
      `}</style>
    </div>
  );
}

export interface ErrorAlertProps {
  error: string | null;
  onDismiss?: () => void;
  className?: string;
}

/**
 * ErrorAlert - 错误提示组件
 *
 * 专门用于显示错误信息
 */
export function ErrorAlert({ error, onDismiss, className }: ErrorAlertProps) {
  if (!error) return null;

  return (
    <div className={cn(className)}>
      <Alert variant="destructive" onDismiss={onDismiss} className="mb-4">
        {error}
      </Alert>
    </div>
  );
}

export interface AlertDescriptionProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * AlertDescription - 警告描述组件
 *
 * 用于在 Alert 内显示详细描述
 */
export function AlertDescription({ children, className }: AlertDescriptionProps) {
  return (
    <div className={cn('text-sm opacity-90 mt-1', className)}>
      {children}
    </div>
  );
}

export interface AlertTitleProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * AlertTitle - 警告标题组件
 *
 * 用于在 Alert 内显示标题
 */
export function AlertTitle({ children, className }: AlertTitleProps) {
  return (
    <h5 className={cn('font-medium leading-none tracking-tight', className)}>
      {children}
    </h5>
  );
}
