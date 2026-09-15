import { useEffect, useState } from "react";

/** Plain browser online/offline events — unrelated to the service worker's
 * own lifecycle (see vite.config.ts). The PWA's app shell can load from
 * cache while offline, but the actual try-on flow always needs a live
 * connection to the backend (see vite.config.ts's NetworkOnly rule for
 * /api/*) — this is what lets the UI say so honestly instead of just
 * failing confusingly on the first API call. */
export function useOnlineStatus(): boolean {
  const [isOnline, setIsOnline] = useState(() => navigator.onLine);

  useEffect(() => {
    const goOnline = () => setIsOnline(true);
    const goOffline = () => setIsOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  return isOnline;
}
