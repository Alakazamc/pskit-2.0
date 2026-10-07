import { LoaderCircle } from "lucide-react";
import "./PageLoading.css";

/** One quiet loading mark inside the retained page shell. */
export function PageLoading({ label }: { label: string }) {
  return <div className="page-loading" role="status" aria-label={label}>
    <span className="page-loading-spinner" aria-hidden="true"><LoaderCircle size={22} /></span>
  </div>;
}

export function PageLoadError({ message, retryLabel, onRetry }: {
  message: string; retryLabel: string; onRetry: () => void;
}) {
  return <div className="page-load-error" role="alert">
    <p>{message}</p><button type="button" className="mono-text-button" onClick={onRetry}>{retryLabel}</button>
  </div>;
}
