import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import type { ReviewWorkflow } from '../src/types.ts';
import { initialAuditIdentityWorkIsComplete } from '../src/utils/initialIdentityAuditWorkflow.ts';

function workflow(phase: string): ReviewWorkflow {
  return {
    schema_version: '1.0.0',
    match_id: 'match-1',
    available: true,
    phase,
    status: 'action_required',
    current_step_id: 'initial_audit',
    review_complete: false,
    can_enter_report: false,
    can_publish: false,
    steps: [],
    required_action: null,
    issues: { blocking: 0, important: 0, optional: 0 },
    freshness: {
      reviewed_identity_current: true,
      reviewed_stats_current: false,
      reviewed_output_current: false,
      qa_approval_current: false,
    },
    blockers: [],
    allowed_actions: phase === 'initial_audit' ? ['identify_players'] : [],
  };
}

test('audit panel stops offering identity mutations once its response leaves initial audit', () => {
  assert.equal(initialAuditIdentityWorkIsComplete(workflow('initial_audit')), false);
  assert.equal(initialAuditIdentityWorkIsComplete(workflow('exceptions')), true);

  const panel = readFileSync(
    new URL('../src/components/InitialIdentityAuditPanel.tsx', import.meta.url),
    'utf8',
  );
  assert.match(panel, /updates: \[\]/);
  assert.match(panel, /session_finished/);
  assert.match(panel, /auditIdentityWorkComplete/);
  assert.match(panel, /Wymagany audyt jest zakończony/);
  assert.match(panel, /initialAuditIdentityWorkIsComplete\(nextStore\.workflow\)/);
});
