import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import type { ReviewWorkflow } from '../src/types.ts';
import {
  identityReviewProgress,
  identityReviewStage,
  workflowAllows,
} from '../src/utils/identityReviewWorkspace.ts';

function workflow(overrides: Partial<ReviewWorkflow>): ReviewWorkflow {
  return {
    schema_version: '1.0.0',
    match_id: 'm1',
    available: true,
    phase: 'initial_audit',
    status: 'action_required',
    current_step_id: 'initial_audit',
    review_complete: false,
    can_enter_report: false,
    can_publish: false,
    steps: [],
    required_action: null,
    issues: { blocking: 0, important: 0, optional: 0 },
    freshness: { reviewed_identity_current: false, reviewed_stats_current: false, reviewed_output_current: false, qa_approval_current: false },
    blockers: [],
    allowed_actions: [],
    ...overrides,
  };
}

test('maps persisted workflow phases to the four operator stages', () => {
  assert.equal(identityReviewStage(workflow({ phase: 'initial_audit' })), 'identify_players');
  assert.equal(identityReviewStage(workflow({ phase: 'exceptions' })), 'remaining_issues');
  assert.equal(identityReviewStage(workflow({ phase: 'ready_to_finalize', status: 'ready' })), 'prepare_result');
  assert.equal(identityReviewStage(workflow({ phase: 'rendering_review_video', status: 'processing' })), 'rendering');
  assert.equal(identityReviewStage(workflow({ phase: 'video_qa' })), 'video_qa');
  assert.equal(identityReviewStage(workflow({ phase: 'complete', status: 'complete', review_complete: true })), 'complete');
});

test('operator CTAs come solely from backend allowed_actions', () => {
  assert.equal(workflowAllows(workflow({ phase: 'ready_to_finalize' }), 'finalize_identity'), false);
  assert.equal(workflowAllows(workflow({ phase: 'ready_to_finalize', allowed_actions: ['finalize_identity'] }), 'finalize_identity'), true);
  assert.equal(workflowAllows(workflow({ phase: 'video_qa', allowed_actions: ['approve_video_qa'] }), 'approve_video_qa'), true);
});

test('progress labels stay friendly and follow workflow step status', () => {
  const steps = identityReviewProgress(workflow({
    steps: [{ id: 'initial_audit', status: 'completed', completed: 5, total: 5, remaining: 0, locked_reason_code: null }],
  }));
  assert.deepEqual(steps.map((step) => step.label), ['Rozpoznaj zawodników', 'Pozostałe przypadki', 'Przygotuj wynik', 'Sprawdź wideo']);
  assert.equal(steps[0].status, 'completed');
  assert.equal(steps[1].status, 'locked');
});

test('normal Step 3 entry renders only the unified workspace before diagnostics', () => {
  const admin = readFileSync(resolve(import.meta.dirname, '../src/components/AdminPanel.tsx'), 'utf8');
  const stable = readFileSync(resolve(import.meta.dirname, '../src/components/StablePlayersPanel.tsx'), 'utf8');
  const workspace = readFileSync(resolve(import.meta.dirname, '../src/components/IdentityReviewWorkspace.tsx'), 'utf8');
  const videoQa = readFileSync(resolve(import.meta.dirname, '../src/components/ReviewedVideoQaPanel.tsx'), 'utf8');
  assert.match(admin, /<IdentityReviewWorkspace/);
  assert.match(admin, /Developer \/ diagnostyka/);
  assert.match(admin, /includeOperatorTools=\{false\}/);
  assert.doesNotMatch(admin, /buildReviewReadiness/);
  assert.match(stable, /includeOperatorTools/);
  assert.equal((workspace.match(/finalizeReviewWorkflow\(/g) || []).length, 1);
  assert.doesNotMatch(workspace, /finalizeReviewedIdentity|generateReviewedOutput/);
  assert.match(videoQa, /onWorkflowChanged\(result\.workflow\)/);
});
