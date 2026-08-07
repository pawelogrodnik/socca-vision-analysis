import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import type { InitialIdentityAuditTelemetryEvent } from '../src/types.ts';
import {
  InitialIdentityAuditFrameBatcher,
  canStageInitialAuditDecision,
  initialAuditBudgetReached,
} from '../src/utils/initialIdentityAuditFrameBatch.ts';
import type { InitialIdentityAuditDecision } from '../src/utils/initialIdentityAudit.ts';

function decision(observationKey: string, playerId: string): InitialIdentityAuditDecision {
  return {
    kind: 'player',
    observationKey,
    playerId,
    playerName: playerId,
    playerNumber: null,
    teamLabel: 'A',
  };
}

function telemetry(eventType: InitialIdentityAuditTelemetryEvent['event_type']): InitialIdentityAuditTelemetryEvent {
  return {
    event_id: `${eventType}-1`,
    session_id: 'session-1',
    event_type: eventType,
  };
}

test('audit decisions stay local until one frame batch is flushed', async () => {
  const batcher = new InitialIdentityAuditFrameBatcher();
  let requests = 0;
  batcher.stageDecision(decision('obs-1', 'roman'));
  batcher.stageDecision(decision('obs-2', 'marek'));
  batcher.stageDecision(decision('obs-3', 'kamil'));
  assert.equal(requests, 0);

  const result = await batcher.flush(async (batch) => {
    requests += 1;
    assert.equal(batch.updates.length, 3);
    return 'saved';
  });
  assert.equal(result, 'saved');
  assert.equal(requests, 1);
});

test('empty frame flush does not create a request', async () => {
  const batcher = new InitialIdentityAuditFrameBatcher();
  let requests = 0;
  const result = await batcher.flush(async () => {
    requests += 1;
    return undefined;
  });
  assert.equal(result, null);
  assert.equal(requests, 0);
});

test('frame batch deduplicates repeated decisions to the final player', async () => {
  const batcher = new InitialIdentityAuditFrameBatcher();
  batcher.stageDecision(decision('obs-1', 'roman'));
  batcher.stageDecision(decision('obs-1', 'marek'));
  batcher.stageDecision(decision('obs-1', 'tomek'));

  await batcher.flush(async (batch) => {
    assert.equal(batch.updates.length, 1);
    assert.equal(batch.updates[0]?.observation_key, 'obs-1');
    assert.equal(batch.updates[0]?.player_id, 'tomek');
    return undefined;
  });
});

test('frame batch includes crop and action telemetry without click-per-request chatter', async () => {
  const batcher = new InitialIdentityAuditFrameBatcher();
  batcher.recordTelemetry(telemetry('crop_clicked'));
  batcher.recordTelemetry(telemetry('action'));
  batcher.recordTelemetry(telemetry('crop_clicked'));
  batcher.stageDecision(decision('obs-1', 'roman'));
  let requests = 0;

  await batcher.flush(async (batch) => {
    requests += 1;
    assert.equal(batch.updates.length, 1);
    assert.deepEqual(batch.telemetryEvents.map((event) => event.event_type), [
      'crop_clicked',
      'action',
      'crop_clicked',
    ]);
    return undefined;
  });
  assert.equal(requests, 1);
});

test('failed frame batch keeps final local changes and telemetry retryable', async () => {
  const batcher = new InitialIdentityAuditFrameBatcher();
  batcher.stageDecision(decision('obs-1', 'tomek'));
  batcher.recordTelemetry(telemetry('action'));
  await assert.rejects(
    batcher.flush(async () => { throw new Error('network unavailable'); }),
    /network unavailable/,
  );

  await batcher.flush(async (batch) => {
    assert.equal(batch.updates.length, 1);
    assert.equal(batch.updates[0]?.player_id, 'tomek');
    assert.deepEqual(batch.telemetryEvents.map((event) => event.event_type), ['action']);
    return undefined;
  });
});

test('server snapshots keep a newer local dirty decision', async () => {
  const batcher = new InitialIdentityAuditFrameBatcher();
  batcher.stageDecision(decision('obs-1', 'roman'));
  await batcher.flush(async () => {
    batcher.stageDecision(decision('obs-2', 'marek'));
    return undefined;
  });

  assert.deepEqual(batcher.mergeServerDecisions({
    'obs-1': decision('obs-1', 'roman'),
  }), {
    'obs-1': decision('obs-1', 'roman'),
    'obs-2': decision('obs-2', 'marek'),
  });
});

test('local operator budget blocks a new decision but permits editing an existing one', () => {
  const current = {
    'obs-1': decision('obs-1', 'roman'),
    'obs-2': decision('obs-2', 'marek'),
  };
  assert.equal(initialAuditBudgetReached(current, false, 2), true);
  assert.equal(
    canStageInitialAuditDecision(current, 'obs-3', decision('obs-3', 'kamil'), false, 2),
    false,
  );
  assert.equal(
    canStageInitialAuditDecision(current, 'obs-1', decision('obs-1', 'tomek'), false, 2),
    true,
  );
});

test('panel flushes before both frame navigation directions and on finish', () => {
  const panel = readFileSync(
    new URL('../src/components/InitialIdentityAuditPanel.tsx', import.meta.url),
    'utf8',
  );
  assert.match(panel, /async function moveFrame/);
  assert.match(panel, /await flushPendingAuditChanges\(\)/);
  assert.match(panel, /\[telemetryEvent\('session_finished'\)\],\s*true/);
  assert.match(panel, /initialAuditIdentityWorkIsComplete\(saved\.workflow\)/);
  assert.match(panel, /frameBatcherRef\.current\.stageClear/);
  assert.doesNotMatch(panel, /enqueueBackgroundSave/);

  const applyAction = panel.slice(
    panel.indexOf('function applyAction'),
    panel.indexOf('function chooseAction'),
  );
  assert.match(applyAction, /frameBatcherRef\.current\.stageDecision/);
  assert.doesNotMatch(applyAction, /saveInitialIdentityAuditSeeds|flushPendingAuditChanges/);
});
