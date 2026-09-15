import { useAuth } from "./hooks/useAuth";
import { useTryOnFlow } from "./hooks/useTryOnFlow";
import HomeScreen from "./screens/HomeScreen";
import ProcessingScreen from "./screens/ProcessingScreen";
import ResultScreen from "./screens/ResultScreen";

export default function App() {
  const auth = useAuth();
  const flow = useTryOnFlow();
  const { submission } = flow;

  if (submission.status === "pending" || submission.status === "processing") {
    return <ProcessingScreen status={submission.status} />;
  }

  if (submission.status === "completed" && flow.personImage) {
    return (
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
  }

  return (
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
      onLogin={auth.login}
      onSignup={auth.signup}
      onLogout={auth.logout}
    />
  );
}
