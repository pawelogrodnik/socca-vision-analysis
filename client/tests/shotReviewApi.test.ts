import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import {
  acceptShotReviewSuggestion,
  createShotReviewShot,
  deleteShotReviewShot,
  editShotReviewShot,
  getShotReviewEditor,
  rejectShotReviewSuggestion,
} from '../src/api.ts';

const originalFetch = globalThis.fetch;

afterEach(() => { globalThis.fetch = originalFetch; });

test('Shot Review API helpers use the canonical editor routes and revision payloads', async () => {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  globalThis.fetch = async (path, init) => {
    calls.push({ path: String(path), init });
    return new Response(JSON.stringify({ revision: 'r2', canonical_shots: [], unreviewed_suggestions: [] }), { headers: { 'content-type': 'application/json' } });
  };
  const shot = { time_sec: 12, team_id: 'corgi', outcome: 'goal' as const, player_id: null };
  await getShotReviewEditor('published one');
  await acceptShotReviewSuggestion('published one', { expected_revision: 'r1', candidate_id: 'candidate', candidate_generation_digest: 'digest', shot });
  await rejectShotReviewSuggestion('published one', { expected_revision: 'r1', candidate_id: 'candidate', candidate_generation_digest: 'digest' });
  await createShotReviewShot('published one', { expected_revision: 'r1', shot });
  await editShotReviewShot('published one', 'shot/one', { expected_revision: 'r1', shot: { ...shot, manual_location_override: { x: 4, y: 5 } } });
  await deleteShotReviewShot('published one', 'shot/one', { expected_revision: 'r1' });

  assert.deepEqual(calls.map(({ path, init }) => [path, init?.method || 'GET']), [
    ['/api/published/matches/published%20one/shot-review/editor', 'GET'],
    ['/api/published/matches/published%20one/shot-review/editor/suggestions/accept', 'POST'],
    ['/api/published/matches/published%20one/shot-review/editor/suggestions/reject', 'POST'],
    ['/api/published/matches/published%20one/shot-review/editor/shots', 'POST'],
    ['/api/published/matches/published%20one/shot-review/editor/shots/shot%2Fone', 'PUT'],
    ['/api/published/matches/published%20one/shot-review/editor/shots/shot%2Fone', 'DELETE'],
  ]);
  assert.deepEqual(JSON.parse(String(calls[4].init?.body)), { expected_revision: 'r1', shot: { ...shot, manual_location_override: { x: 4, y: 5 } } });
});
