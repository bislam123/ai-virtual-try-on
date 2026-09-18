import { useEffect, useState } from "react";
import { getUsage } from "../api/usageClient";
import type { UsageStatus } from "../types/usage";

interface UsageIndicatorProps {
  authToken: string | null;
}

function describe(usage: UsageStatus): string {
  if (usage.remaining_today !== null) {
    return `${usage.remaining_today} of ${usage.limit_per_day} free generations left today`;
  }
  if (usage.remaining_this_month !== null) {
    return `${usage.remaining_this_month} of ${usage.limit_per_month} generations left this month`;
  }
  return "Unlimited generations";
}

/** Brief sections 4/23's "Usage quota" stage, made visible: shows the
 * user's real remaining allowance before they hit it, not just as a 429
 * surprise on submit. Fails silently on error — this is a nice-to-have
 * status line, not something worth alarming the user about if it can't
 * load. Re-fetches whenever authToken changes (signing in/out changes
 * whose usage applies) — HomeScreen naturally remounts after a submission
 * (it's swapped out for the processing/result screens meanwhile), so a
 * fresh mount-time fetch is enough to reflect newly-consumed usage too. */
export default function UsageIndicator({ authToken }: UsageIndicatorProps) {
  const [usage, setUsage] = useState<UsageStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUsage(authToken)
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch(() => {
        if (!cancelled) setUsage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [authToken]);

  if (!usage) return null;

  const isLow = (usage.remaining_today ?? usage.remaining_this_month ?? 1) === 0;

  return (
    <p className={`text-center text-xs ${isLow ? "font-medium text-amber-600" : "text-slate-500"}`}>
      {describe(usage)}
      {usage.plan === "free" && usage.remaining_today === 0 && " — premium plans are coming soon"}
    </p>
  );
}
