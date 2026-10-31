/**
 * Motion gating for the three-dimensional scene.
 *
 * Animation stops when the tab is hidden and when the viewer has asked for
 * reduced motion. Both are required by `docs/UI_SPEC.md`, and neither affects
 * measurement: the simulator advances on the server, so a paused scene changes
 * what is drawn and nothing else.
 */

import { useEffect, useState } from 'react';

export interface SceneMotion {
  /** Whether per-frame animation should run at all. */
  animate: boolean;
  /** True when the viewer asked for reduced motion. */
  reducedMotion: boolean;
  /** True when the document is hidden. */
  hidden: boolean;
}

export function useSceneMotion(): SceneMotion {
  const [reducedMotion, setReducedMotion] = useState(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  });
  const [hidden, setHidden] = useState(() =>
    typeof document === 'undefined' ? false : document.hidden,
  );

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = (event: MediaQueryListEvent) => setReducedMotion(event.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  useEffect(() => {
    const onVisibility = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  return { animate: !reducedMotion && !hidden, reducedMotion, hidden };
}

/** Whether this browser can give us a WebGL context at all. */
export function detectWebgl(): boolean {
  if (typeof document === 'undefined') return false;
  try {
    const canvas = document.createElement('canvas');
    return Boolean(
      canvas.getContext('webgl2') ??
        canvas.getContext('webgl') ??
        canvas.getContext('experimental-webgl'),
    );
  } catch {
    return false;
  }
}
