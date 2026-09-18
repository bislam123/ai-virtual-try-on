import { useState } from "react";
import OfflineBanner from "./components/OfflineBanner";
import UpdatePrompt from "./components/UpdatePrompt";
import { useAuth } from "./hooks/useAuth";
import { useOnlineStatus } from "./hooks/useOnlineStatus";
import { usePwaUpdate } from "./hooks/usePwaUpdate";
import { useTryOnFlow } from "./hooks/useTryOnFlow";
import AdminScreen from "./screens/AdminScreen";
import HomeScreen from "./screens/HomeScreen";
import ProcessingScreen from "./screens/ProcessingScreen";
import ResetPasswordScreen from "./screens/ResetPasswordScreen";
import ResultScreen from "./screens/ResultScreen";

// A password reset email's link points here as `/?reset_token=<token>` (see
// backend/app/api/auth.py's forgot_password) -- the same query-param
// handoff pattern HomeScreen already uses for the browser extension (see
// its initialGarmentSource), not a separate route: this app has no router
// and no server-side SPA-fallback config for a dedicated path, so a query
// param on the existing root is the one URL shape guaranteed to work
// across dev, the PWA service worker, and whatever static hosting this
// eventually ships behind.
function readResetToken(): string | null {
  return new URLSearchParams(window.location.search).get("reset_token");
}

export default function App() {
  const auth = useAuth();
  const flow = useTryOnFlow();
  const isOnline = useOnlineStatus();
  const pwaUpdate = usePwaUpdate();
  const { submission } = flow;
  const [resetToken, setResetToken] = useState<string | null>(() => readResetToken());
  const [showAdmin, setShowAdmin] = useState(false);

  const dismissResetPassword = () => {
    const url = new URL(window.location.href);
    url.searchParams.delete("reset_token");
    window.history.replaceState({}, "", url.pathname + url.search);
    setResetToken(null);
  };

  let screen;
  if (resetToken) {
    screen = <ResetPasswordScreen token={resetToken} onDone={dismissResetPassword} />;
  } else if (showAdmin && auth.user?.is_admin && auth.token) {
    // Frontend gate is convenience navigation only, not the security
    // boundary -- every /api/admin/* call this screen makes is
    // independently re-authorized server-side (get_current_admin_user).
    // Checked here anyway so a stale showAdmin=true (e.g. after logout)
    // can never render a screen that would just fail its own requests.
    screen = <AdminScreen authToken={auth.token} onClose={() => setShowAdmin(false)} />;
  } else if (submission.status === "pending" || submission.status === "processing") {
    screen = <ProcessingScreen status={submission.status} onCancel={() => void flow.cancel(auth.token)} />;
  } else if (submission.status === "completed" && flow.personImage) {
    screen = (
      <ResultScreen
        personImage={flow.personImage}
        jobId={submission.jobId}
        saved={submission.saved}
        authToken={auth.token}
        onSaved={flow.markSaved}
        onTryAnother={flow.tryAnother}
        onChangeClothing={flow.changeClothing}
      />
    );
  } else {
    screen = (
      <HomeScreen
        personImage={flow.personImage}
        setPersonImage={flow.setPersonImage}
        garmentImage={flow.garmentImage}
        setGarmentImage={flow.setGarmentImage}
        category={flow.category}
        setCategory={flow.setCategory}
        errorMessage={submission.status === "failed" ? submission.message : null}
        onDismissError={flow.dismissError}
        onSubmit={() => void flow.submit(auth.token)}
        user={auth.user}
        authToken={auth.token}
        onLogin={auth.login}
        onSignup={auth.signup}
        onForgotPassword={auth.forgotPassword}
        onLogout={auth.logout}
        onDeleteAccount={auth.deleteAccount}
        onOpenAdmin={auth.user?.is_admin ? () => setShowAdmin(true) : undefined}
      />
    );
  }

  return (
    <>
      {pwaUpdate.needRefresh && <UpdatePrompt onUpdate={pwaUpdate.applyUpdate} />}
      {!isOnline && <OfflineBanner />}
      {screen}
    </>
  );
}
