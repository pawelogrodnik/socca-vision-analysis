import type {
  InitialIdentityAuditSeedUpdate,
  InitialIdentityAuditTelemetryEvent,
} from '../types';
import {
  initialIdentityAuditClearUpdate,
  initialIdentityAuditSeedUpdate,
  type InitialIdentityAuditDecision,
} from './initialIdentityAudit';

type DirtyDecisionChange = {
  decision: InitialIdentityAuditDecision | null;
  update: InitialIdentityAuditSeedUpdate;
};

export type InitialIdentityAuditFrameBatch = {
  updates: InitialIdentityAuditSeedUpdate[];
  telemetryEvents: InitialIdentityAuditTelemetryEvent[];
};

export class InitialIdentityAuditFrameBatcher {
  private readonly dirtyChanges = new Map<string, DirtyDecisionChange>();
  private pendingTelemetry: InitialIdentityAuditTelemetryEvent[] = [];

  reset(): void {
    this.dirtyChanges.clear();
    this.pendingTelemetry = [];
  }

  stageDecision(decision: InitialIdentityAuditDecision): void {
    this.dirtyChanges.set(decision.observationKey, {
      decision,
      update: initialIdentityAuditSeedUpdate(decision),
    });
  }

  stageClear(observationKey: string): void {
    this.dirtyChanges.set(observationKey, {
      decision: null,
      update: initialIdentityAuditClearUpdate(observationKey),
    });
  }

  recordTelemetry(event: InitialIdentityAuditTelemetryEvent): void {
    this.pendingTelemetry.push(event);
  }

  hasPendingChanges(additionalTelemetry: InitialIdentityAuditTelemetryEvent[] = []): boolean {
    return this.dirtyChanges.size > 0
      || this.pendingTelemetry.length > 0
      || additionalTelemetry.length > 0;
  }

  mergeServerDecisions(
    serverDecisions: Record<string, InitialIdentityAuditDecision>,
  ): Record<string, InitialIdentityAuditDecision> {
    const merged = { ...serverDecisions };
    for (const [observationKey, change] of this.dirtyChanges) {
      if (change.decision) merged[observationKey] = change.decision;
      else delete merged[observationKey];
    }
    return merged;
  }

  async flush<T>(
    save: (batch: InitialIdentityAuditFrameBatch) => Promise<T>,
    additionalTelemetry: InitialIdentityAuditTelemetryEvent[] = [],
  ): Promise<T | null> {
    const changes = new Map(this.dirtyChanges);
    const telemetryEvents = [...this.pendingTelemetry, ...additionalTelemetry];
    if (changes.size === 0 && telemetryEvents.length === 0) return null;

    this.pendingTelemetry = [];
    const batch = {
      updates: [...changes.values()].map((change) => change.update),
      telemetryEvents,
    };
    try {
      const result = await save(batch);
      for (const [observationKey, change] of changes) {
        if (this.dirtyChanges.get(observationKey) === change) {
          this.dirtyChanges.delete(observationKey);
        }
      }
      return result;
    } catch (error) {
      this.pendingTelemetry = [...telemetryEvents, ...this.pendingTelemetry];
      throw error;
    }
  }
}

export function initialAuditBudgetReached(
  decisions: Record<string, InitialIdentityAuditDecision>,
  backendReached: boolean,
  limit: number | undefined,
): boolean {
  if (backendReached) return true;
  if (limit === undefined) return false;
  return Object.values(decisions).filter((decision) => decision.kind !== 'skip').length >= limit;
}

export function canStageInitialAuditDecision(
  decisions: Record<string, InitialIdentityAuditDecision>,
  observationKey: string,
  decision: InitialIdentityAuditDecision,
  backendReached: boolean,
  limit: number | undefined,
): boolean {
  return decision.kind === 'skip'
    || Boolean(decisions[observationKey])
    || !initialAuditBudgetReached(decisions, backendReached, limit);
}
