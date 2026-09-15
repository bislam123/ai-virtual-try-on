export default function OfflineBanner() {
  return (
    <div
      role="status"
      className="bg-amber-100 px-4 py-2 text-center text-sm font-medium text-amber-800"
      style={{ paddingTop: "calc(0.5rem + env(safe-area-inset-top, 0px))" }}
    >
      You're offline — Try It On needs an internet connection to generate results.
    </div>
  );
}
