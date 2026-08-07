import assert from 'node:assert/strict';
import test from 'node:test';

import type { ReviewWorkflow } from '../src/types.ts';
import { reportWorkflowGate } from '../src/lib/reviewWorkflowGating.ts';


function workflow(overrides: Partial<ReviewWorkflow>): ReviewWorkflow {
  return {
    schema_version: '1.0.0',
    match_id: 'm1',
    available: true,
    phase: 'video_qa',
    status: 'action_required',
    current_step_id: 'video_qa',
    review_complete: false,
    can_enter_report: false,
    can_publish: false,
    steps: [],
    required_action: null,
    issues: { blocking: 0, important: 0, optional: 0 },
    freshness: { reviewed_identity_current: true, reviewed_stats_current: true, reviewed_output_current: true, qa_approval_current: false },
    blockers: [{ code: 'video_qa_not_approved', step_id: 'video_qa', user_actionable: true, details: {} }],
    allowed_actions: ['approve_video_qa'],
    ...overrides,
  };
}

test('Step 4 stays locked when backend workflow is incomplete', () => {
  const gate = reportWorkflowGate(workflow({}));
  assert.equal(gate.allowed, false);
  assert.equal(gate.reasonCode, 'video_qa_not_approved');
});

test('Step 4 unlocks only after backend workflow says complete', () => {
  const gate = reportWorkflowGate(workflow({ phase: 'complete', status: 'complete', review_complete: true, can_enter_report: true, can_publish: true, blockers: [] }));
  assert.equal(gate.allowed, true);
  assert.equal(gate.reasonCode, null);
});

test('legacy local readiness cannot override missing backend workflow', () => {
  const gate = reportWorkflowGate(null);
  assert.equal(gate.allowed, false);
  assert.equal(gate.reasonCode, 'review_not_completed');
});
