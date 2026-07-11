import type { Match } from '../types';

export type ReviewReadinessStatus =
  | 'needs_analysis'
  | 'needs_review_data'
  | 'warnings'
  | 'ready'
  | 'published';

export type ReviewReadinessCheck = {
  key: string;
  label: string;
  ready: boolean;
  required: boolean;
};

export type ReviewReadiness = {
  status: ReviewReadinessStatus;
  statusLabel: string;
  checks: ReviewReadinessCheck[];
  missingRequired: string[];
  warnings: string[];
  blocking: boolean;
  readyForPackage: boolean;
  readyForPublish: boolean;
};

export function buildReviewReadiness(match: Match | null): ReviewReadiness {
  if (!match) {
    return {
      status: 'needs_analysis',
      statusLabel: 'needs analysis',
      checks: [],
      missingRequired: ['match'],
      warnings: [],
      blocking: true,
      readyForPackage: false,
      readyForPublish: false,
    };
  }
  const stableOverlay = Boolean(
    match.analysis_report?.artifacts?.stable_overlay_preview || match.match_package?.assets?.stable_overlay_preview,
  );
  const stableOverlaySkipped = match.analysis_report?.parameters?.render_stable_overlay === false;
  const checks: ReviewReadinessCheck[] = [
    {
      key: 'analysis_report',
      label: 'Analysis completed',
      ready: match.analysis_report?.status === 'completed',
      required: true,
    },
    {
      key: 'stable_players',
      label: 'Stable players',
      ready: Boolean(match.stable_players),
      required: true,
    },
    {
      key: 'team_config',
      label: 'Team config',
      ready: Boolean(match.team_config),
      required: true,
    },
    {
      key: 'player_identity_assignments',
      label: 'Player identity assignments',
      ready: Boolean(match.player_identity_assignments),
      required: true,
    },
    {
      key: 'resolved_player_stats',
      label: 'Resolved player stats',
      ready: Boolean(match.resolved_player_stats),
      required: true,
    },
    {
      key: 'stable_overlay_preview',
      label: stableOverlaySkipped ? 'Stable overlay skipped' : 'Stable overlay preview',
      ready: stableOverlay || stableOverlaySkipped,
      required: !stableOverlaySkipped,
    },
  ];
  const missingRequired = checks
    .filter((check) => check.required && !check.ready)
    .map((check) => check.key);
  const warnings = reviewWarnings(match);
  const blocking = missingRequired.length > 0;
  const published = Boolean(match.published_match_id || match.status === 'published');
  const status: ReviewReadinessStatus = published
    ? 'published'
    : blocking
      ? match.analysis_report?.status === 'completed'
        ? 'needs_review_data'
        : 'needs_analysis'
      : warnings.length > 0
        ? 'warnings'
        : 'ready';
  return {
    status,
    statusLabel: statusToLabel(status),
    checks,
    missingRequired,
    warnings,
    blocking,
    readyForPackage: !blocking,
    readyForPublish: !blocking,
  };
}

function reviewWarnings(match: Match): string[] {
  const warnings: string[] = [];
  const identity = match.player_identity_assignments;
  const assignments = identity?.assignments || [];
  const assignedRealPlayers = assignments.filter(
    (assignment) => assignment.status === 'assigned' && Boolean(assignment.player_id),
  ).length;
  if (identity && assignedRealPlayers === 0) {
    warnings.push('Brak przypisanych realnych zawodnikow. To dozwolone, ale profile zawodnikow beda puste.');
  }
  const summary = identity?.summary || {};
  const conflictsTotal = Number(summary.conflicts_total || 0);
  if (conflictsTotal > 0) {
    warnings.push(`Identity review ma ${conflictsTotal} konfliktow team/player.`);
  }
  const packageValidation = match.match_package?.package_validation;
  if (packageValidation?.status === 'warnings') {
    warnings.push(...(packageValidation.warnings || []));
  }
  return Array.from(new Set(warnings));
}

function statusToLabel(status: ReviewReadinessStatus): string {
  if (status === 'needs_analysis') return 'needs analysis';
  if (status === 'needs_review_data') return 'needs assignments/config';
  if (status === 'warnings') return 'warnings';
  if (status === 'published') return 'published';
  return 'ready';
}
