import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { getMergedMatchExternalVideo, getStaticPublicMatchReport } from '../src/api.ts';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function publicReport(id: string, sourceMatchId = id) {
  return {
    schema_version: '0.1.0',
    generated_at: '2026-09-07T00:00:00+00:00',
    id,
    source_match_id: sourceMatchId,
    report_type: 'public_match_report',
    match: { id: sourceMatchId, title: 'Public report' },
    teams: [],
    players: [],
  };
}

test('static public reports load for physical and canonical merged IDs without a delivery special case', async () => {
  for (const [id, sourceMatchId] of [
    ['published-9c7485e4', '9c7485e4'],
    ['published-merged-canonical', 'published-merged-canonical'],
  ]) {
    globalThis.fetch = (async (input) => {
      assert.equal(String(input), `/published/matches/${id}/public_report.json`);
      return Response.json(publicReport(id, sourceMatchId));
    }) as typeof fetch;

    const report = await getStaticPublicMatchReport(id);
    assert.equal(report.id, id);
    assert.equal(report.report_type, 'public_match_report');
  }
});

test('static public report turns a Vercel SPA HTML fallback into an artifact error', async () => {
  globalThis.fetch = (async () => new Response('<!doctype html><html><body>SPA</body></html>', {
    status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8' },
  })) as typeof fetch;

  await assert.rejects(
    getStaticPublicMatchReport('missing'),
    /Published report artifact was not found in this deployment\./,
  );
});

test('static public report rejects HTML even when a proxy labels it as JSON', async () => {
  globalThis.fetch = (async () => new Response('<!doctype html><html><body>SPA</body></html>', {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })) as typeof fetch;

  await assert.rejects(
    getStaticPublicMatchReport('missing'),
    /Published report artifact was not found in this deployment\./,
  );
});

test('static public report controls malformed JSON even with a JSON content type', async () => {
  globalThis.fetch = (async () => new Response('{"report":', {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })) as typeof fetch;

  await assert.rejects(
    getStaticPublicMatchReport('missing'),
    /Published report artifact is invalid in this deployment\./,
  );
});

test('static public report gives a clear not-found error for a 404 response', async () => {
  globalThis.fetch = (async () => new Response('Not found', { status: 404 })) as typeof fetch;

  await assert.rejects(
    getStaticPublicMatchReport('missing'),
    /Published report artifact was not found in this deployment\./,
  );
});

test('merged external video falls back to a public-safe static sidecar when the API is unavailable', async () => {
  const id = 'published-merged-canonical';
  globalThis.fetch = (async (input) => {
    const path = String(input);
    if (path.endsWith(`/api/published/matches/${id}/external-video`)) {
      return Response.json({ detail: 'offline' }, { status: 503 });
    }
    assert.equal(path, `/published/matches/${id}/external_video.json`);
    return Response.json({
      status: 'current',
      external_video: {
        provider: 'youtube',
        video_id: 'AbCdEfGhI_1',
        source_url: 'https://www.youtube.com/watch?v=AbCdEfGhI_1',
        embed_url: 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1',
      },
    });
  }) as typeof fetch;

  const state = await getMergedMatchExternalVideo(id);
  assert.equal(state.status, 'current');
  assert.equal(state.external_video?.embed_url, 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1');
});

test('missing static merged external-video sidecar is a normal not_configured state', async () => {
  globalThis.fetch = (async () => new Response('Not found', { status: 404 })) as typeof fetch;
  const state = await getMergedMatchExternalVideo('published-merged-missing');
  assert.equal(state.status, 'not_configured');
  assert.equal(state.external_video, null);
});
