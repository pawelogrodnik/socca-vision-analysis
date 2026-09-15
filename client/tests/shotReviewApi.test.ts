import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import {
  acceptShotReviewSuggestionCluster,
  createShotReviewShot,
  deleteShotReviewShot,
  editShotReviewShot,
  getShotReviewFrameLocationContext,
  getShotReviewEditor,
  projectShotReviewFrameLocation,
  rejectShotReviewSuggestionCluster,
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
  await getShotReviewFrameLocationContext('published one', 12.5);
  await projectShotReviewFrameLocation('published one', { logical_frame_time_sec: 12.5, x_px: 50, y_px: 25, frame_width: 100, frame_height: 50 });
  await acceptShotReviewSuggestionCluster('published one', { expected_revision: 'r1', cluster_id: 'cluster', candidate_generation_digest: 'digest', shot });
  await rejectShotReviewSuggestionCluster('published one', { expected_revision: 'r1', cluster_id: 'cluster', candidate_generation_digest: 'digest' });
  await createShotReviewShot('published one', { expected_revision: 'r1', shot });
  await editShotReviewShot('published one', 'shot/one', { expected_revision: 'r1', shot: { ...shot, manual_location_override: { x: 4, y: 5 } } });
  await deleteShotReviewShot('published one', 'shot/one', { expected_revision: 'r1' });

  assert.deepEqual(calls.map(({ path, init }) => [path, init?.method || 'GET']), [
    ['/api/published/matches/published%20one/shot-review/editor', 'GET'],
    ['/api/published/matches/published%20one/shot-review/editor/frame-location?logical_frame_time_sec=12.5', 'GET'],
    ['/api/published/matches/published%20one/shot-review/editor/frame-location/project', 'POST'],
    ['/api/published/matches/published%20one/shot-review/editor/suggestion-clusters/accept', 'POST'],
    ['/api/published/matches/published%20one/shot-review/editor/suggestion-clusters/reject', 'POST'],
    ['/api/published/matches/published%20one/shot-review/editor/shots', 'POST'],
    ['/api/published/matches/published%20one/shot-review/editor/shots/shot%2Fone', 'PUT'],
    ['/api/published/matches/published%20one/shot-review/editor/shots/shot%2Fone', 'DELETE'],
  ]);
  assert.deepEqual(JSON.parse(String(calls[2].init?.body)), { logical_frame_time_sec: 12.5, x_px: 50, y_px: 25, frame_width: 100, frame_height: 50 });
  assert.deepEqual(JSON.parse(String(calls[6].init?.body)), { expected_revision: 'r1', shot: { ...shot, manual_location_override: { x: 4, y: 5 } } });
});
