import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

// virtual:pwa-register/react doesn't resolve under vitest at all (it's a
// build-time virtual module vite-plugin-pwa only wires up for `vite dev`/
// `vite build`) -- confirmed directly, not assumed: importing it
// unmocked in a vitest file throws before a single test even runs. Every
// test here mocks it for that reason, not as a style choice.
const mockUpdateServiceWorker = vi.fn();
const mockUseRegisterSW = vi.fn();

vi.mock("virtual:pwa-register/react", () => ({
  useRegisterSW: () => mockUseRegisterSW(),
}));

import { usePwaUpdate } from "./usePwaUpdate";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("usePwaUpdate", () => {
  it("reflects needRefresh=false when no update is available", () => {
    mockUseRegisterSW.mockReturnValue({
      needRefresh: [false, vi.fn()],
      offlineReady: [false, vi.fn()],
      updateServiceWorker: mockUpdateServiceWorker,
    });

    const { result } = renderHook(() => usePwaUpdate());

    expect(result.current.needRefresh).toBe(false);
  });

  it("reflects needRefresh=true once vite-plugin-pwa reports a waiting update", () => {
    mockUseRegisterSW.mockReturnValue({
      needRefresh: [true, vi.fn()],
      offlineReady: [false, vi.fn()],
      updateServiceWorker: mockUpdateServiceWorker,
    });

    const { result } = renderHook(() => usePwaUpdate());

    expect(result.current.needRefresh).toBe(true);
  });

  it("applyUpdate() calls the underlying updateServiceWorker(true) -- the only thing that ever triggers a reload", () => {
    mockUseRegisterSW.mockReturnValue({
      needRefresh: [true, vi.fn()],
      offlineReady: [false, vi.fn()],
      updateServiceWorker: mockUpdateServiceWorker,
    });

    const { result } = renderHook(() => usePwaUpdate());
    result.current.applyUpdate();

    expect(mockUpdateServiceWorker).toHaveBeenCalledWith(true);
    expect(mockUpdateServiceWorker).toHaveBeenCalledTimes(1);
  });
});
