import * as React from 'react';
import { Sun, Moon, Monitor } from '@phosphor-icons/react';
import { useThemeStore } from '@/stores/themeStore';
import { cn } from '@/lib/utils';

interface ThemeToggleProps {
  className?: string;
}

export function ThemeToggle({ className }: ThemeToggleProps) {
  const { theme, resolvedTheme, setTheme } = useThemeStore();

  const options: { value: typeof theme; icon: React.ReactNode; label: string }[] = [
    { value: 'light', icon: <Sun className="h-4 w-4" />, label: 'Light' },
    { value: 'dark', icon: <Moon className="h-4 w-4" />, label: 'Dark' },
    { value: 'system', icon: <Monitor className="h-4 w-4" />, label: 'System' },
  ];

  return (
    <div className={cn('flex items-center gap-1 rounded-lg bg-muted p-1', className)}>
      {options.map((option) => (
        <button
          key={option.value}
          onClick={() => setTheme(option.value)}
          className={cn(
            'flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-all',
            theme === option.value
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          )}
          title={option.label}
        >
          {option.icon}
          <span className="hidden sm:inline">{option.label}</span>
        </button>
      ))}
    </div>
  );
}

// Simple toggle button that just toggles between light and dark
export function ThemeToggleSimple({ className }: ThemeToggleProps) {
  const { resolvedTheme, toggleTheme } = useThemeStore();

  return (
    <button
      onClick={toggleTheme}
      className={cn(
        'flex h-8 w-8 items-center justify-center rounded-lg',
        'bg-muted text-muted-foreground hover:text-foreground',
        'transition-colors hover:bg-muted/80',
        className
      )}
      title={resolvedTheme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
    >
      {resolvedTheme === 'light' ? (
        <Sun className="h-4 w-4" />
      ) : (
        <Moon className="h-4 w-4" />
      )}
    </button>
  );
}
