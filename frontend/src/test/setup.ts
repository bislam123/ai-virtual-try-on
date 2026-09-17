import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// globals: false in vite.config.ts's test config means Vitest doesn't inject
// a global afterEach, which is what @testing-library/react's own automatic
// cleanup detection relies on -- register it explicitly instead, so DOM
// nodes from one test never leak into the next.
afterEach(() => {
  cleanup();
});
