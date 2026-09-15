import OfflineBanner from "./components/OfflineBanner";
import { useAuth } from "./hooks/useAuth";
import { useOnlineStatus } from "./hooks/useOnlineStatus";
import { useTryOnFlow } from "./hooks/useTryOnFlow";
import HomeScreen from "./screens/HomeScreen";
import ProcessingScreen from "./screens/ProcessingScreen";
import ResultScreen from "./screens/ResultScreen";

export default function App() {
  const auth = useAuth();
  const flow = useTryOnFlow();
  const isOnline = useOnlineStatus();
  const { submission } = flow;

  let screen;
  if (submission.status === "pending" || submission.status === "processing") {
    screen = <ProcessingScreen status={submission.status} />;
  } else if (submission.status === "completed" && flow.personImage) {
    screen = (
      <ResultScreen
        personImage={flow.personImage}
        jobId={submission.jobId}
        resultUrl={submission.resultUrl}
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
        onLogout={auth.logout}
      />
    );
  }

  return (
    <>
      {!isOnline && <OfflineBanner />}
      {screen}
    </>
  );
}
