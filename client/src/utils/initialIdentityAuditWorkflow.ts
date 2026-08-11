import type { InitialIdentityAuditDocument, ReviewWorkflow } from '../types';

export type InitialAuditPendingTarget = {
  frameIndex: number;
  observationKey: string;
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
): string[] {
  const requiredKeys = workflow?.initial_audit?.required_case_observation_keys ?? [];
  return requiredKeys.filter((observationKey) => !decisions[observationKey]);
}

export function initialAuditFinalizeOutcome(
  workflow: ReviewWorkflow | undefined,
  decisions: Readonly<Record<string, unknown>>,
  document: InitialIdentityAuditDocument,
): InitialAuditFinalizeOutcome {
  const complete = initialAuditIdentityWorkIsComplete(workflow);
  const pendingRequiredKeys = initialAuditPendingRequiredKeys(workflow, decisions);
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

  return {
    complete,
    completed: finiteCount(evidence?.completed, 0),
    total: finiteCount(evidence?.total, pendingRequiredKeys.length),
    remaining: finiteCount(evidence?.remaining, pendingRequiredKeys.length),
    pendingRequiredKeys,
    missingRequiredKeys,
    firstPendingTarget,
  };
}

function finiteCount(value: number | undefined, fallback: number): number {
  return Number.isFinite(value) ? Math.max(0, Number(value)) : fallback;
}
