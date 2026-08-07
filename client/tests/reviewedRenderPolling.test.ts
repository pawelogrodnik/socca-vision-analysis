import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import type { ReviewedOutputJob } from '../src/types.ts';
import {
  createReviewedRenderStatusPolling,
  RENDER_STATUS_POLL_INTERVAL_MS,
} from '../src/utils/reviewedRenderPolling.ts';


const runningJob = { status: 'running' } as ReviewedOutputJob;
const completedJob = { status: 'completed' } as ReviewedOutputJob;
const failedJob = { status: 'failed' } as ReviewedOutputJob;

test('reviewed render polling waits 30 seconds and only loads render status', () => {
  const timers: Array<{ callback: () => void; delayMs: number }> = [];
  const polling = createReviewedRenderStatusPolling({
    loadStatus: async () => runningJob,
    onStatus: () => undefined,
    onTerminalStatus: () => undefined,
    onError: () => undefined,
    setTimer: (callback, delayMs) => {
      timers.push({ callback, delayMs });
      return timers.length;
    },
    clearTimer: () => undefined,
  });

  polling.start();

  assert.equal(timers.length, 1);
  assert.equal(timers[0].delayMs, RENDER_STATUS_POLL_INTERVAL_MS);
});

test('reviewed render polling has no overlapping status requests', async () => {
  let resolveStatus: ((job: ReviewedOutputJob) => void) | undefined;
  let loadCalls = 0;
  const polling = createReviewedRenderStatusPolling({
    loadStatus: () => {
      loadCalls += 1;
      return new Promise<ReviewedOutputJob>((resolve) => { resolveStatus = resolve; });
    },
    onStatus: () => undefined,
    onTerminalStatus: () => undefined,
    onError: () => undefined,
    setTimer: () => 1,
    clearTimer: () => undefined,
  });

  const firstRequest = polling.pollNow();
  await polling.pollNow();
  assert.equal(loadCalls, 1);

  resolveStatus?.(runningJob);
  await firstRequest;
  assert.equal(loadCalls, 1);
});

test('completed or failed status stops polling and triggers one final refresh', async () => {
  for (const terminalJob of [completedJob, failedJob]) {
    let loadCalls = 0;
    let terminalCalls = 0;
    const polling = createReviewedRenderStatusPolling({
      loadStatus: async () => {
        loadCalls += 1;
        return terminalJob;
      },
      onStatus: () => undefined,
      onTerminalStatus: () => { terminalCalls += 1; },
      onError: () => undefined,
      setTimer: () => 1,
      clearTimer: () => undefined,
    });

    await polling.pollNow();
    await polling.pollNow();

    assert.equal(loadCalls, 1);
    assert.equal(terminalCalls, 1);
  }
});

test('stopping reviewed render polling clears the pending timer', () => {
  let clearedTimer: number | undefined;
  const polling = createReviewedRenderStatusPolling({
    loadStatus: async () => runningJob,
    onStatus: () => undefined,
    onTerminalStatus: () => undefined,
    onError: () => undefined,
    setTimer: () => 42,
    clearTimer: (timerId) => { clearedTimer = timerId; },
  });

  polling.start();
  polling.stop();

  assert.equal(clearedTimer, 42);
});

test('reviewed output panel polls only status and keeps correction progress event-driven', () => {
  const panel = readFileSync(
    resolve(import.meta.dirname, '../src/components/ReviewedMatchOutputPanel.tsx'),
    'utf8',
  );

  assert.match(panel, /loadStatus: \(\) => getReviewedOutputStatus\(matchId\)/);
  assert.match(panel, /onTerminalStatus: \(\) => \{ void refresh\(\); \}/);
  assert.doesNotMatch(panel, /setInterval\(\(\) => \{ void refresh\(\); \}, 1500\)/);
  assert.match(panel, /setProgress\(result\.review_progress\)/);
});
