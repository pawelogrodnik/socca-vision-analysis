import type { ReviewedOutputJob } from '../types';

export const RENDER_STATUS_POLL_INTERVAL_MS = 30_000;

type TimerId = ReturnType<typeof window.setTimeout>;

type RenderStatusPollingOptions = {
  loadStatus: () => Promise<ReviewedOutputJob>;
  onStatus: (job: ReviewedOutputJob) => void;
  onTerminalStatus: (job: ReviewedOutputJob) => void;
  onError: (error: unknown) => void;
  setTimer?: (callback: () => void, delayMs: number) => TimerId;
  clearTimer?: (timerId: TimerId) => void;
};

export function isReviewedRenderInProgress(status: ReviewedOutputJob['status'] | undefined): boolean {
  return status === 'queued' || status === 'running';
}

export function isReviewedRenderTerminal(status: ReviewedOutputJob['status']): boolean {
  return status === 'completed' || status === 'failed';
}

export function createReviewedRenderStatusPolling({
  loadStatus,
  onStatus,
  onTerminalStatus,
  onError,
  setTimer = window.setTimeout.bind(window),
  clearTimer = window.clearTimeout.bind(window),
}: RenderStatusPollingOptions) {
  let timerId: TimerId | undefined;
  let stopped = false;
  let requestInFlight = false;
  let terminalStatusHandled = false;

  function scheduleNext(): void {
    if (stopped || requestInFlight || terminalStatusHandled || timerId !== undefined) return;
    timerId = setTimer(() => {
      timerId = undefined;
      void pollNow();
    }, RENDER_STATUS_POLL_INTERVAL_MS);
  }

  async function pollNow(): Promise<void> {
    if (stopped || requestInFlight || terminalStatusHandled) return;
    requestInFlight = true;
    try {
      const job = await loadStatus();
      if (stopped) return;
      onStatus(job);
      if (isReviewedRenderTerminal(job.status)) {
        terminalStatusHandled = true;
        onTerminalStatus(job);
      } else if (!isReviewedRenderInProgress(job.status)) {
        terminalStatusHandled = true;
      }
    } catch (error) {
      if (!stopped) onError(error);
    } finally {
      requestInFlight = false;
      scheduleNext();
    }
  }

  return {
    start: scheduleNext,
    stop: () => {
      stopped = true;
      if (timerId !== undefined) clearTimer(timerId);
      timerId = undefined;
    },
    pollNow,
  };
}
