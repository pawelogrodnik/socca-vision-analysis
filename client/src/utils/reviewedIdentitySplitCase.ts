import type { MixedPlayerCase, ReviewedCorrectionContext } from '../types';

/** Converts a server-owned correction context into the shared split-editor case. */
export function correctionContextAsSplitCase(context: ReviewedCorrectionContext): MixedPlayerCase {
  const crops = context.visual_evidence?.anchor_crops || [];
  const cropFrames = crops.map((crop) => crop.frame);
  return {
    case_id: context.concurrent_resolution?.parent_case_id,
    candidate_subject_id: context.candidate_subject_id,
    original_issue: 'mixed_players',
    mixed_hint: 'unknown',
    resolution_status: 'unresolved',
    source_subject_digest: context.source_ownership_digest || '',
    source_tracklet_ids: context.tracklet_ids,
    observation_count: context.detected_observation_count || 0,
    frame_start: context.frame_start ?? (cropFrames.length > 0 ? Math.min(...cropFrames) : 0),
    frame_end: context.frame_end ?? (cropFrames.length > 0 ? Math.max(...cropFrames) : 0),
    temporal_topology: context.temporal_topology || null,
    action_capabilities: context.action_capabilities,
    temporal_evidence: { status: context.visual_evidence?.status || 'missing', anchor_crops: crops },
  };
}
