/// <reference types="node" />
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Reads vite.config.ts as source text rather than importing/executing it:
// this is a *configuration* safety check (are the settings that actually
// govern the generated service worker's caching behavior still what we
// intend), not a runtime integration test -- vitest/jsdom has no real
// service worker to run one against anyway. The properties asserted here
// were also verified directly against the real, built dist/sw.js output
// (see docs/ARCHITECTURE.md's PWA section) -- this test exists so a
// future change to vite.config.ts that silently breaks one of them fails
// loudly here, rather than only being caught by someone remembering to
// re-inspect a build output by hand.
//
// process.cwd(), not import.meta.url -- vitest's own module transform
// doesn't give import.meta.url a real file:// URL for this file, so
// resolving relative to it throws; vitest always runs with the frontend/
// project root as the working directory (see package.json's `test`
// script), which vite.config.ts sits at the root of.
const configPath = resolve(process.cwd(), "vite.config.ts");
const configSource = readFileSync(configPath, "utf-8");

describe("vite.config.ts — service worker cache safety", () => {
  it("routes every /api/ request through NetworkOnly, never a caching strategy", () => {
    // Covers auth responses, try-on job status, result images, and any
    // other API data alike -- the route is keyed on the path prefix, not
    // enumerated per endpoint, so nothing under /api/ can be missed.
    expect(configSource).toMatch(/url\.pathname\.startsWith\(['"]\/api\/['"]\)/);
    expect(configSource).toMatch(/handler:\s*['"]NetworkOnly['"]/);
  });

  it("declares exactly one runtimeCaching rule -- nothing else could accidentally intercept /api/ or cache auth data", () => {
    const ruleCount = (configSource.match(/urlPattern:/g) ?? []).length;
    expect(ruleCount).toBe(1);
  });

  it("only precaches static build-output file types, never dynamic/API content", () => {
    const globPatterns = configSource.match(/globPatterns:\s*\[([^\]]+)\]/);
    expect(globPatterns).not.toBeNull();
    // A known, closed set of static asset extensions -- never a bare
    // "**/*" (which could sweep up something unintended) and never a
    // pattern reaching into anything that isn't this build's own output.
    expect(globPatterns![1]).toMatch(/\{js,css,html,svg,png,ico,webmanifest\}/);
  });

  it("uses prompt-based update handling, not silent autoUpdate", () => {
    // 'autoUpdate' was confirmed (by building and inspecting the real
    // generated sw.js) to call self.skipWaiting() unconditionally at top
    // level, taking control of open tabs with no reload signal at all --
    // 'prompt' + a disabled auto-injected register script is what makes
    // hooks/usePwaUpdate.ts's explicit, user-initiated update flow the
    // only path that can ever activate a new worker.
    expect(configSource).toMatch(/registerType:\s*'prompt'/);
    expect(configSource).toMatch(/injectRegister:\s*false/);
  });
});
