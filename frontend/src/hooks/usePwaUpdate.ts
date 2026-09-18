import { useRegisterSW } from "virtual:pwa-register/react";

/** Thin wrapper around vite-plugin-pwa's own React hook (see
 * vite.config.ts's registerType: 'prompt' + injectRegister: false) --
 * extracted to its own hook, matching this app's existing convention
 * (useAuth, useTryOnFlow, useOnlineStatus), so App.tsx stays simple and
 * this is mockable in isolation rather than depending on the real
 * virtual:pwa-register/react module resolving under every test
 * environment.
 *
 * `needRefresh` becomes true once a new service worker has installed and
 * is waiting -- the current tab is still running the old app version
 * until `applyUpdate()` is called. That's the whole point: this never
 * reloads on its own. `applyUpdate()` sends the new worker a skip-waiting
 * message; vite-plugin-pwa's own registration code (see its build/register.ts)
 * listens for that worker actually taking control and reloads the page
 * exactly once at that point -- not before, and not repeatedly (there is
 * nothing here that could loop: needRefresh only ever transitions
 * false -> true once per detected update, and the reload itself
 * re-initializes the whole app against the new worker/new needRefresh
 * state).
 */
export function usePwaUpdate() {
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW();

  return {
    needRefresh,
    applyUpdate: () => {
      void updateServiceWorker(true);
    },
  };
}
