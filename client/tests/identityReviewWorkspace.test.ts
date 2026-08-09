import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import type { Match, ReviewWorkflow } from '../src/types.ts';
import {
  suggestedTopLevelStep,
  topLevelStepStatus,
} from '../src/utils/adminWorkflowNavigation.ts';
import {
  hasOperatorReviewableVisualEvidence,
  identityReviewProgress,
  identityReviewStage,
  workflowAllows,
} from '../src/utils/identityReviewWorkspace.ts';
import {
  reportWorkflowOperatorCopy,
  requiredCasesLabel,
  reviewWorkflowOperatorCopy,
} from '../src/utils/reviewWorkflowPresentation.ts';

function analyzedMatch(overrides: Partial<Match> = {}): Match {
  return {
    id: 'm1',
    title: 'Mecz',
    status: 'analyzed',
    analysis_report: { status: 'completed' },
    ...overrides,
  } as Match;
}

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

test('only an existing anchor crop makes an identity conflict operator-reviewable', () => {
  assert.equal(hasOperatorReviewableVisualEvidence({
    visual_evidence: { anchor_crops: [] },
  }), false);
  assert.equal(hasOperatorReviewableVisualEvidence({
    visual_evidence: {
      anchor_crops: [{
        anchor_crop_id: 'crop-1',
        artifact: 'crops/crop-1.jpg',
        frame: 42,
      }],
    },
  }), true);
});

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

test('published history never bypasses incomplete current review', () => {
  const published = analyzedMatch({ published_match_id: 'old-published-copy' });
  const incomplete = workflow({ can_enter_report: false });
  assert.equal(suggestedTopLevelStep(published, incomplete), 'review');
  assert.equal(topLevelStepStatus('publish', 'publish', published, incomplete), 'locked');
  assert.equal(suggestedTopLevelStep(published, null), 'review');
  const complete = workflow({
    phase: 'complete',
    status: 'complete',
    review_complete: true,
    can_enter_report: true,
    can_publish: true,
  });
  assert.equal(suggestedTopLevelStep(published, complete), 'publish');
  assert.equal(topLevelStepStatus('publish', 'publish', published, complete), 'current');
});

test('operator-facing workflow copy never exposes machine state codes', () => {
  const cases = workflow({
    phase: 'exceptions',
    required_action: { type: 'review_identity_issue', step_id: 'exceptions', remaining: 2 },
    issues: { blocking: 2, important: 2, optional: 0 },
  });
  assert.equal(requiredCasesLabel(1), '1 przypadek wymaga sprawdzenia');
  assert.equal(requiredCasesLabel(2), '2 przypadki wymagają sprawdzenia');
  assert.equal(requiredCasesLabel(5), '5 przypadków wymaga sprawdzenia');
  assert.equal(reviewWorkflowOperatorCopy(cases), '2 przypadki wymagają sprawdzenia');
  assert.equal(reviewWorkflowOperatorCopy(workflow({ required_action: { type: 'retry_review_recompute', step_id: 'exceptions' } })), 'Review wymaga odświeżenia');
  assert.equal(reportWorkflowOperatorCopy(cases), 'Najpierw zakończ Review');
  for (const text of [reviewWorkflowOperatorCopy(cases), reportWorkflowOperatorCopy(cases)]) {
    assert.doesNotMatch(text, /review_identity_issue|identity_issues_remaining|retry_review_recompute/);
  }
});

test('normal Step 3 entry renders only the unified workspace before diagnostics', () => {
  const admin = readFileSync(resolve(import.meta.dirname, '../src/components/AdminPanel.tsx'), 'utf8');
  const stable = readFileSync(resolve(import.meta.dirname, '../src/components/StablePlayersPanel.tsx'), 'utf8');
  const workspace = readFileSync(resolve(import.meta.dirname, '../src/components/IdentityReviewWorkspace.tsx'), 'utf8');
  const videoQa = readFileSync(resolve(import.meta.dirname, '../src/components/ReviewedVideoQaPanel.tsx'), 'utf8');
  const exceptions = readFileSync(resolve(import.meta.dirname, '../src/components/IdentityExceptionReviewPanel.tsx'), 'utf8');
  const report = readFileSync(resolve(import.meta.dirname, '../src/components/MatchReportPage.tsx'), 'utf8');
  assert.match(admin, /<IdentityReviewWorkspace/);
  assert.match(admin, /Developer \/ diagnostyka/);
  assert.match(admin, /showDeveloperDiagnostics &&/);
  assert.match(admin, /includeOperatorTools=\{false\}/);
  assert.doesNotMatch(admin, /buildReviewReadiness/);
  assert.match(stable, /includeOperatorTools/);
  assert.equal((workspace.match(/finalizeReviewWorkflow\(/g) || []).length, 1);
  assert.doesNotMatch(workspace, /finalizeReviewedIdentity|generateReviewedOutput/);
  assert.match(videoQa, /onWorkflowChanged\(result\.workflow\)/);
  assert.match(workspace, /Sprawdź wideo ponownie/);
  assert.match(workspace, /showApprovedVideo/);
  assert.match(videoQa, /workflow\.phase === 'complete'/);
  assert.doesNotMatch(exceptions, /isActionableSubjectReviewCard/);
  assert.match(exceptions, /reviewCase && entity && hasVisualEvidence/);
  assert.match(exceptions, /Decyzja nie obejmie sąsiednich ani niejednoznacznych klatek/);
  assert.match(exceptions, /Brak materiału pozwalającego wiarygodnie rozstrzygnąć ten przypadek/);
  assert.match(exceptions, /ten przypadek nie powinien wymagać ręcznej decyzji/);
  assert.match(exceptions, /Nie udało się przygotować podglądu przypadku wymagającego decyzji/);
  assert.match(exceptions, /requiredCasesLabel\(workflow\.issues\.blocking\)/);
  assert.doesNotMatch(exceptions, /workflow\.issues\.blocking \+ workflow\.issues\.important/);
  assert.doesNotMatch(report, /onBuildPackage|createMatchPackage/);
});
