import type { InitialIdentityAuditDocument, ReviewWorkflow } from '../types';

export type InitialAuditPendingTarget = {
  frameIndex: number;
  observationKey: string;
};

export type InitialAuditIncompleteFinalizeEvidence = {
  requiredKeys: string[];
  decidedRequiredCountAtFinalize: number;
  completed: number;
  total: number;
  remaining: number;
};

export type InitialAuditFinalizeOutcome = {
  complete: boolean;
  completed: number;
  total: number;
  remaining: number;
  pendingRequiredKeys: string[];
  missingRequiredKeys: string[];
  firstPendingTarget: InitialAuditPendingTarget | null;
};

export function initialAuditIdentityWorkIsComplete(
  workflow: ReviewWorkflow | undefined,
): boolean {
  return workflow !== undefined && workflow.phase !== 'initial_audit';
}

export function initialAuditPendingRequiredKeys(
  workflow: ReviewWorkflow | undefined,
  decisions: Readonly<Record<string, unknown>>,
  incompleteFinalizeEvidence?: InitialAuditIncompleteFinalizeEvidence | null,
): string[] {
  const requiredKeys = incompleteFinalizeEvidence?.requiredKeys
    ?? workflow?.initial_audit?.required_case_observation_keys
    ?? [];
  return requiredKeys.filter(
    (observationKey) => !hasExplicitDecision(decisions, observationKey),
  );
}

export function buildInitialAuditIncompleteFinalizeEvidence(
  workflow: ReviewWorkflow | undefined,
  decisions: Readonly<Record<string, unknown>>,
): InitialAuditIncompleteFinalizeEvidence | null {
  if (!workflow || initialAuditIdentityWorkIsComplete(workflow)) return null;
  const evidence = workflow.initial_audit;
  const requiredKeys = evidence?.required_case_observation_keys ?? [];
  return {
    requiredKeys,
    decidedRequiredCountAtFinalize: requiredKeys.filter(
      (observationKey) => hasExplicitDecision(decisions, observationKey),
    ).length,
    completed: finiteCount(evidence?.completed, 0),
    total: finiteCount(evidence?.total, requiredKeys.length),
    remaining: finiteCount(evidence?.remaining, requiredKeys.length),
  };
}

export function initialAuditFinalizeOutcome(
  workflow: ReviewWorkflow | undefined,
  decisions: Readonly<Record<string, unknown>>,
  document: InitialIdentityAuditDocument,
  incompleteFinalizeEvidence?: InitialAuditIncompleteFinalizeEvidence | null,
): InitialAuditFinalizeOutcome {
  const complete = initialAuditIdentityWorkIsComplete(workflow);
  const pendingRequiredKeys = initialAuditPendingRequiredKeys(
    workflow,
    decisions,
    incompleteFinalizeEvidence,
  );
  const targets = new Map<string, InitialAuditPendingTarget>();
  document.frames.forEach((frame, frameIndex) => {
    frame.observations.forEach((observation) => {
      targets.set(observation.observation_key, {
        frameIndex,
        observationKey: observation.observation_key,
      });
    });
  });
  const firstPendingTarget = pendingRequiredKeys
    .map((observationKey) => targets.get(observationKey))
    .find((target): target is InitialAuditPendingTarget => Boolean(target)) ?? null;
  const missingRequiredKeys = pendingRequiredKeys.filter(
    (observationKey) => !targets.has(observationKey),
  );
  const evidence = workflow?.initial_audit;
  const localDecisionDelta = incompleteFinalizeEvidence
    ? incompleteFinalizeEvidence.requiredKeys.filter(
      (observationKey) => hasExplicitDecision(decisions, observationKey),
    ).length - incompleteFinalizeEvidence.decidedRequiredCountAtFinalize
    : 0;
  const completed = incompleteFinalizeEvidence
    ? finiteCount(incompleteFinalizeEvidence.completed + localDecisionDelta, 0)
    : finiteCount(evidence?.completed, 0);
  const total = incompleteFinalizeEvidence
    ? finiteCount(incompleteFinalizeEvidence.total, pendingRequiredKeys.length)
    : finiteCount(evidence?.total, pendingRequiredKeys.length);
  const remaining = incompleteFinalizeEvidence
    ? finiteCount(
      incompleteFinalizeEvidence.remaining - localDecisionDelta,
      pendingRequiredKeys.length,
    )
    : finiteCount(evidence?.remaining, pendingRequiredKeys.length);

  return {
    complete,
    completed: Math.min(total, completed),
    total,
    remaining,
    pendingRequiredKeys,
    missingRequiredKeys,
    firstPendingTarget,
  };
}

function hasExplicitDecision(
  decisions: Readonly<Record<string, unknown>>,
  observationKey: string,
): boolean {
  return decisions[observationKey] !== undefined
    && decisions[observationKey] !== null;
}

function finiteCount(value: number | undefined, fallback: number): number {
  return Number.isFinite(value) ? Math.max(0, Number(value)) : fallback;
}
