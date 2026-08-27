import { useEffect, useRef, useState, type ImgHTMLAttributes } from 'react';

type Props = ImgHTMLAttributes<HTMLImageElement> & {
  src: string;
  maxRetries?: number;
  retryDelay?: number;
};

/** Retries a crop while the backend finishes its just-in-time materialization. */
export function ReviewedEvidenceImage({
  src,
  maxRetries = 10,
  retryDelay = 1_000,
  onError,
  ...props
}: Props) {
  const [retry, setRetry] = useState(0);
  const retryTimerRef = useRef<number | null>(null);

  useEffect(() => {
    setRetry(0);
    return () => {
      if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    };
  }, [src]);

  // Keep this request separate from a pre-materialization 404 cached by an
  // older view. Further retries get their own URL as well.
  const retrySrc = `${src}${src.includes('?') ? '&' : '?'}review_evidence=1&retry=${retry}`;

  return <img
    {...props}
    src={retrySrc}
    onError={(event) => {
      onError?.(event);
      if (retry >= maxRetries || retryTimerRef.current !== null) return;
      const nextRetry = retry + 1;
      retryTimerRef.current = window.setTimeout(() => {
        retryTimerRef.current = null;
        setRetry(nextRetry);
      }, retryDelay);
    }}
  />;
}
