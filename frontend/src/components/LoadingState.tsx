type LoadingStateProps = {
  title?: string;
  message: string;
  compact?: boolean;
};

export function LoadingState({
  title = "Loading",
  message,
  compact = false,
}: LoadingStateProps) {
  return (
    <div className={`loading-state ${compact ? "loading-state--compact" : ""}`}>
      <span className="loading-state__spinner" aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <div>{message}</div>
      </div>
    </div>
  );
}
