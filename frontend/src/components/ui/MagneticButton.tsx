import * as React from 'react';
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion';

interface MagneticButtonProps {
  children: React.ReactElement<{ onMouseMove?: (e: React.MouseEvent) => void; onMouseLeave?: (e: React.MouseEvent) => void; onClick?: () => void }>;
  className?: string;
  strength?: number;
  disabled?: boolean;
}

export function MagneticButton({
  children,
  strength = 0.3,
  disabled = false,
}: MagneticButtonProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [isHovered, setIsHovered] = React.useState(false);

  // Motion values for smooth animation
  const x = useMotionValue(0);
  const y = useMotionValue(0);

  // Spring configuration for natural feel
  const springConfig = { stiffness: 150, damping: 15, mass: 0.1 };
  const springX = useSpring(x, springConfig);
  const springY = useSpring(y, springConfig);

  // Check for reduced motion preference
  const [prefersReducedMotion, setPrefersReducedMotion] = React.useState(false);

  React.useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    setPrefersReducedMotion(mediaQuery.matches);
  }, []);

  const handleMouseMove = React.useCallback(
    (e: React.MouseEvent) => {
      if (disabled || prefersReducedMotion || !ref.current) return;

      const rect = ref.current.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;

      const distanceX = e.clientX - centerX;
      const distanceY = e.clientY - centerY;

      x.set(distanceX * strength);
      y.set(distanceY * strength);
    },
    [disabled, prefersReducedMotion, strength, x, y]
  );

  const handleMouseLeave = React.useCallback(() => {
    x.set(0);
    y.set(0);
    setIsHovered(false);
  }, [x, y]);

  const handleMouseEnter = React.useCallback(() => {
    setIsHovered(true);
  }, []);

  const handleClick = React.useCallback(() => {
    // Tactile feedback animation
    x.set(0);
    y.set(0);
  }, [x, y]);

  // If reduced motion is preferred, return child without animation
  if (prefersReducedMotion || disabled) {
    return <>{children}</>;
  }

  return (
    <motion.div
      ref={ref}
      style={{
        x: springX,
        y: springY,
        display: 'inline-flex',
      }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onMouseEnter={handleMouseEnter}
      onClick={handleClick}
      whileTap={{ scale: 0.95 }}
      transition={{ type: 'spring', stiffness: 400, damping: 25 }}
    >
      {React.cloneElement(children, {
        ...children.props,
        onMouseMove: handleMouseMove,
        onMouseLeave: handleMouseLeave,
      })}
    </motion.div>
  );
}
