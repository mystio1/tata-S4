import '@testing-library/jest-dom/vitest';

/**
 * jsdom has no canvas implementation, so `getContext` throws and logs a long
 * "not implemented" trace. Returning null is exactly what a browser without
 * WebGL does, and it is the condition the application's two-dimensional
 * fallback is built for, so stubbing it here tests the real fallback path
 * quietly rather than suppressing a genuine failure.
 */
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;

/** jsdom does not implement ResizeObserver, which the chart container uses. */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
