import assert from 'node:assert/strict';
import test from 'node:test';

import type { ReviewedCorrectionResponse, ReviewWorkflow } from '../src/types.ts';
import {
  canFinalizeReviewedVideo,
  reviewedCorrectionWorkflowPresentation,
} from '../src/utils/reviewedOutputWorkflow.ts';

function workflow(overrides: Partial<ReviewWorkflow> = {}): ReviewWorkflow {
  return {
    schema_version: '1.0.0',
    match_id: 'match-1',
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
    freshness: {
      reviewed_identity_current: true,
      reviewed_stats_current: true,
      reviewed_output_current: true,
      qa_approval_current: false,
    },
    blockers: [],
    allowed_actions: [],
    ...overrides,
  };
}

function correctionResponse(nextWorkflow: ReviewWorkflow): ReviewedCorrectionResponse {
  return {
    saved_decision: null,
    effective_action: 'unresolved',
    allocated_stable_slot_id: null,
    snapshot: { status: 'partial_reviewed', stale: false },
    semantic_decision_digest: 'decision',
    review_progress: {
      schema_version: '1.0.0',
      status: 'ready',
      match_id: 'match-1',
      summary: {
        review_units_total: 0,
        review_units_completed: 0,
        review_units_actionable_total: 0,
        completed_by_operator: 0,
        completed_automatically: 0,
        important_decisions_remaining: 0,
        optional_cases_remaining: 0,
        structural_blockers: 0,
        ignored_low_impact: 0,
        operator_decisions_saved: 0,
        operator_queue_completion_ratio: 1,
      },
      observations: { operator_reviewed_observation_ratio: 0 },
      next_cases: [],
      technical_diagnostics: {
        candidate_subjects: 0,
        tracklets: 0,
        unresolved_tracklet_assignments: 0,
      },
    },
    decision_impact: {
      affected_tracklets: 0,
      affected_detected_observations: 0,
      important_decisions_remaining_after: 0,
    },
    workflow: nextWorkflow,
  };
}

test('QA correction follows the automatically queued reviewed-video job', () => {
  const queued = { status: 'queued' as const, job_key: 'next-render' };
  const result = correctionResponse(workflow({
    phase: 'rendering_review_video',
    status: 'processing',
    processing: queued,
  }));
  const presentation = reviewedCorrectionWorkflowPresentation(result);
  assert.equal(presentation.mode, 'automatic_rerender');
  assert.deepEqual(presentation.queuedRenderJob, queued);
});

test('blocker correction never assumes a queued render', () => {
  const result = correctionResponse(workflow({
    phase: 'exceptions',
    issues: { blocking: 1, important: 1, optional: 0 },
  }));
  const presentation = reviewedCorrectionWorkflowPresentation(result);
  assert.equal(presentation.mode, 'exceptions');
  assert.equal(presentation.queuedRenderJob, null);
});

test('expensive reviewed-video actions follow authoritative workflow permission', () => {
  assert.equal(canFinalizeReviewedVideo(workflow()), false);
  assert.equal(
    canFinalizeReviewedVideo(workflow({
      phase: 'ready_to_finalize',
      status: 'ready',
      allowed_actions: ['finalize_identity'],
    })),
    true,
  );
});
