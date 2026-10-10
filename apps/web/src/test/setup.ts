import "@testing-library/jest-dom/vitest";

class ResizeObserverStub implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub;
Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
