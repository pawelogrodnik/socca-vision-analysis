import assert from 'node:assert/strict';
import test from 'node:test';

import { RequiredFinalSaveQueue } from '../src/utils/requiredFinalSaveQueue.ts';

test('successful final save calls finish exactly once', async () => {
  const queue = new RequiredFinalSaveQueue<{ version: number }>();
  let finishCalls = 0;
  const saved = await queue.finalize(
    async () => ({ version: 2 }),
    async (store) => {
      finishCalls += 1;
      assert.equal(store.version, 2);
    },
  );
  assert.equal(saved.version, 2);
  assert.equal(finishCalls, 1);
});

test('failed final save never calls finish', async () => {
  const queue = new RequiredFinalSaveQueue<{ version: number }>();
  let finishCalls = 0;
  await assert.rejects(
    queue.finalize(
      async () => {
        throw new Error('save failed');
      },
      async () => {
        finishCalls += 1;
      },
    ),
    /save failed/,
  );
  assert.equal(finishCalls, 0);
});

test('final save waits for pending autosave', async () => {
  const queue = new RequiredFinalSaveQueue<string>();
  const order: string[] = [];
  let releasePending: (() => void) | undefined;
  const pending = new Promise<void>((resolve) => {
    releasePending = resolve;
  });
  void queue.enqueue(async () => {
    order.push('autosave-start');
    await pending;
    order.push('autosave-end');
    return 'autosave';
  });
  const finalized = queue.finalize(
    async () => {
      order.push('final-save');
      return 'final';
    },
    async () => {
      order.push('finish');
    },
  );
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(order, ['autosave-start']);
  releasePending?.();
  await finalized;
  assert.deepEqual(
    order,
    ['autosave-start', 'autosave-end', 'final-save', 'finish'],
  );
});

test('double finalize shares one save and one transition', async () => {
  const queue = new RequiredFinalSaveQueue<string>();
  let saves = 0;
  let finishes = 0;
  let releaseSave: (() => void) | undefined;
  const saveGate = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });
  const finalSave = async () => {
    saves += 1;
    await saveGate;
    return 'saved';
  };
  const finish = async () => {
    finishes += 1;
  };
  const first = queue.finalize(finalSave, finish);
  const second = queue.finalize(finalSave, finish);
  assert.strictEqual(first, second);
  releaseSave?.();
  await Promise.all([first, second]);
  assert.equal(saves, 1);
  assert.equal(finishes, 1);
});
