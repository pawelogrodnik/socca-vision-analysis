import type { CSSProperties } from 'react';

import type {
  InitialIdentityAuditObservation,
  InitialIdentityAuditRosterPlayer,
  InitialIdentityAuditRosterTeam,
  InitialIdentityAuditSeedUpdate,
  InitialIdentityAuditStoredDecision,
  VideoMetadata,
} from '../types';

export type InitialIdentityAuditDetectedTeam = 'A' | 'B' | 'U';

export type InitialIdentityAuditAction =
  | {
      kind: 'player';
      playerId: string;
      playerName: string;
      playerNumber?: string | null;
      teamLabel: 'A' | 'B' | 'U';
      suggestionContext?: InitialIdentityAuditSeedUpdate['suggestion_context'];
    }
  | { kind: 'team_unknown'; teamLabel: 'A' | 'B' }
  | { kind: 'referee' | 'false_detection' | 'skip' };

export type InitialIdentityAuditDecision = InitialIdentityAuditAction & {
  observationKey: string;
};

export function initialIdentityAuditPlayerUsedElsewhereInFrame(
  currentFrameObservationKeys: Iterable<string>,
  decisions: Record<string, InitialIdentityAuditDecision>,
  targetObservationKey: string | null,
  playerId: string,
): boolean {
  const frameKeys = new Set(currentFrameObservationKeys);
  return Object.values(decisions).some((decision) => (
    decision.kind === 'player'
    && decision.playerId === playerId
    && decision.observationKey !== targetObservationKey
    && frameKeys.has(decision.observationKey)
  ));
}

export function canApplyInitialIdentityAuditAction(
  currentFrameObservationKeys: Iterable<string>,
  decisions: Record<string, InitialIdentityAuditDecision>,
  targetObservationKey: string,
  action: InitialIdentityAuditAction,
): boolean {
  return action.kind !== 'player' || !initialIdentityAuditPlayerUsedElsewhereInFrame(
    currentFrameObservationKeys,
    decisions,
    targetObservationKey,
    action.playerId,
  );
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function createInitialIdentityAuditEventId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `audit-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function initialIdentityAuditActionLabel(
  action: InitialIdentityAuditAction,
): string {
  if (action.kind === 'player') {
    return action.playerNumber
      ? `${action.playerName} #${action.playerNumber}`
      : action.playerName;
  }
  if (action.kind === 'team_unknown') {
    return `Team ${action.teamLabel} - zawodnik nieznany`;
  }
  if (action.kind === 'referee') return 'Sedzia';
  if (action.kind === 'false_detection') return 'Falszywa detekcja';
  return 'Pomin / nie wiem';
}

export function initialIdentityAuditPlayerAction(
  player: InitialIdentityAuditRosterPlayer,
  team: InitialIdentityAuditRosterTeam,
): InitialIdentityAuditAction {
  return {
    kind: 'player',
    playerId: player.player_id,
    playerName: player.player_name,
    playerNumber: player.player_number,
    teamLabel: team.team_label,
  };
}

export function initialIdentityAuditDecisionFromStored(
  stored: InitialIdentityAuditStoredDecision,
): InitialIdentityAuditDecision | null {
  if (stored.action === 'assign_roster_player' && stored.assigned_player) {
    return {
      kind: 'player',
      observationKey: stored.observation_key,
      playerId: stored.assigned_player.player_id,
      playerName: stored.assigned_player.player_name,
      playerNumber: stored.assigned_player.player_number,
      teamLabel: stored.assigned_team?.team_label ?? 'U',
      suggestionContext: stored.suggestion_context,
    };
  }
  if (stored.action === 'team_a_unknown' || stored.action === 'team_b_unknown') {
    return {
      kind: 'team_unknown',
      observationKey: stored.observation_key,
      teamLabel: stored.action === 'team_a_unknown' ? 'A' : 'B',
    };
  }
  if (
    stored.action === 'referee'
    || stored.action === 'false_detection'
    || stored.action === 'skip'
  ) {
    return {
      kind: stored.action,
      observationKey: stored.observation_key,
    };
  }
  return null;
}

export function initialIdentityAuditDecisionMap(
  storedDecisions: InitialIdentityAuditStoredDecision[],
): Record<string, InitialIdentityAuditDecision> {
  const result: Record<string, InitialIdentityAuditDecision> = {};
  for (const stored of storedDecisions) {
    const decision = initialIdentityAuditDecisionFromStored(stored);
    if (decision) {
      result[decision.observationKey] = decision;
    }
  }
  return result;
}

export function initialIdentityAuditSeedUpdate(
  decision: InitialIdentityAuditDecision,
): InitialIdentityAuditSeedUpdate {
  if (decision.kind === 'player') {
    return {
      update_id: createInitialIdentityAuditEventId(),
      observation_key: decision.observationKey,
      action: 'assign_roster_player',
      player_id: decision.playerId,
      suggestion_context: decision.suggestionContext,
    };
  }
  return {
    update_id: createInitialIdentityAuditEventId(),
    observation_key: decision.observationKey,
    action: decision.kind === 'team_unknown'
      ? decision.teamLabel === 'A'
        ? 'team_a_unknown'
        : 'team_b_unknown'
      : decision.kind,
  };
}

export function initialIdentityAuditClearUpdate(
  observationKey: string,
): InitialIdentityAuditSeedUpdate {
  return {
    update_id: createInitialIdentityAuditEventId(),
    observation_key: observationKey,
    action: 'clear',
  };
}

export function observationBoxStyle(
  observation: InitialIdentityAuditObservation,
  video: VideoMetadata,
): CSSProperties {
  const [x1, y1, x2, y2] = observation.bbox_xyxy;
  return {
    left: `${(clamp(x1, 0, video.width) / video.width) * 100}%`,
    top: `${(clamp(y1, 0, video.height) / video.height) * 100}%`,
    width: `${(clamp(x2 - x1, 1, video.width) / video.width) * 100}%`,
    height: `${(clamp(y2 - y1, 1, video.height) / video.height) * 100}%`,
  };
}

/**
 * Presentation must use the detector's persisted team label. In particular,
 * do not derive this from a later roster decision: the border is a quick way
 * for the operator to audit the detector itself.
 */
export function initialIdentityAuditObservationTeam(
  observation: Pick<InitialIdentityAuditObservation, 'team_label'>,
): InitialIdentityAuditDetectedTeam {
  const team = String(observation.team_label || '').toUpperCase();
  return team === 'A' || team === 'B' ? team : 'U';
}

export function initialIdentityAuditTeamClass(
  observation: Pick<InitialIdentityAuditObservation, 'team_label'>,
): string {
  const team = initialIdentityAuditObservationTeam(observation);
  return team === 'A'
    ? 'team-a'
    : team === 'B'
      ? 'team-b'
      : 'team-unknown';
}

export function initialIdentityAuditObservationBoxClassName(
  observation: Pick<InitialIdentityAuditObservation, 'team_label'>,
  options: { selected: boolean; decided: boolean },
): string {
  return [
    'initial-identity-observation-box',
    initialIdentityAuditTeamClass(observation),
    options.selected ? 'selected' : '',
    options.decided ? 'decided' : '',
  ].filter(Boolean).join(' ');
}

export function observationCropLayout(
  observation: InitialIdentityAuditObservation,
  video: VideoMetadata,
): {
  aspectRatio: string;
  imageStyle: CSSProperties;
  targetBoxStyle: CSSProperties;
} {
  const [x1, y1, x2, y2] = observation.bbox_xyxy;
  const bboxWidth = Math.max(1, x2 - x1);
  const bboxHeight = Math.max(1, y2 - y1);
  // Keep enough of the nearby pitch for orientation, but make the person the
  // primary visual signal. The former loose crop made a selected player tiny.
  const horizontalPadding = bboxWidth * 0.28;
  const verticalPadding = bboxHeight * 0.18;
  const cropX = clamp(x1 - horizontalPadding, 0, video.width - 1);
  const cropY = clamp(y1 - verticalPadding, 0, video.height - 1);
  const cropRight = clamp(x2 + horizontalPadding, cropX + 1, video.width);
  const cropBottom = clamp(y2 + verticalPadding, cropY + 1, video.height);
  const cropWidth = cropRight - cropX;
  const cropHeight = cropBottom - cropY;

  return {
    aspectRatio: `${cropWidth} / ${cropHeight}`,
    imageStyle: {
      width: `${(video.width / cropWidth) * 100}%`,
      maxWidth: 'none',
      left: `${(-cropX / cropWidth) * 100}%`,
      top: `${(-cropY / cropHeight) * 100}%`,
    },
    targetBoxStyle: {
      left: `${((x1 - cropX) / cropWidth) * 100}%`,
      top: `${((y1 - cropY) / cropHeight) * 100}%`,
      width: `${(bboxWidth / cropWidth) * 100}%`,
      height: `${(bboxHeight / cropHeight) * 100}%`,
    },
  };
}
