import type { AnalysisPayload, Match, RuntimeInfo } from '../types';
import {
  ANALYSIS_PRESETS,
  buildAnalysisPreflight,
  type AnalysisPresetId,
  type PreflightCheckLevel,
} from '../lib/analysisPreflight';

type AnalysisPreflightPanelProps = {
  match: Match;
  analysis: AnalysisPayload;
  runtimeInfo: RuntimeInfo | null;
  hasSavedPitchConfig: boolean;
  hasPendingPitchPoints: boolean;
  canRun: boolean;
  disabled: boolean;
  isRunning: boolean;
  onApplyPreset: (presetId: AnalysisPresetId) => void;
  onRun: () => Promise<void> | void;
};

const levelLabels: Record<PreflightCheckLevel, string> = {
  ok: 'OK',
  info: 'Info',
  warning: 'Uwaga',
  blocking: 'Blokada',
};

export function AnalysisPreflightPanel({
  match,
  analysis,
  runtimeInfo,
  hasSavedPitchConfig,
  hasPendingPitchPoints,
  canRun,
  disabled,
  isRunning,
  onApplyPreset,
  onRun,
}: AnalysisPreflightPanelProps) {
  const preflight = buildAnalysisPreflight(match, analysis, runtimeInfo, {
    hasSavedPitchConfig,
    hasPendingPitchPoints,
  });
  const canStart = canRun && preflight.canStart && !disabled;
  const runtimeDevice = analysis.yolo_device || 'auto';
  const stableOverlayNote = analysis.render_stable_overlay
    ? 'stable overlay MP4'
    : 'bez stable overlay MP4';
  const storageNote = analysis.include_ball
    ? `Artefakty beda zawieraly JSON-y trackingowe i pilki (${stableOverlayNote}). Przy pelnym meczu pilka wyraznie zwieksza czas i rozmiar outputu.`
    : `Artefakty beda zawieraly JSON-y trackingowe (${stableOverlayNote}). To jest najbezpieczniejszy pierwszy run pelnego meczu.`;

  return (
    <div className='preflight-panel'>
      <div className='row between'>
        <div>
          <h3>Production preflight</h3>
          <p className='muted'>
            Sprawdz zakres, chunking, runtime i koszt YOLO przed dluga analiza.
          </p>
        </div>
        <button type='button' onClick={onRun} disabled={!canStart}>
          {isRunning ? 'Analiza w toku...' : 'Start background analysis'}
        </button>
      </div>

      <div className='preset-grid'>
        {ANALYSIS_PRESETS.map((preset) => (
          <button
            key={preset.id}
            type='button'
            className='preset-button secondary'
            onClick={() => onApplyPreset(preset.id)}
            disabled={disabled}
          >
            <strong>{preset.label}</strong>
            <span>{preset.description}</span>
          </button>
        ))}
      </div>

      <div className='preflight-metrics'>
        <Metric
          label='Video'
          value={`${formatResolution(preflight.videoWidth, preflight.videoHeight)} / ${formatDuration(preflight.videoDurationSec)}`}
          detail={`${formatNumber(preflight.videoFps, 2)} fps, ${formatInteger(preflight.videoFrameCount)} frames`}
        />
        <Metric
          label='Zakres'
          value={preflight.analyzesFullVideo ? 'Pelny film' : formatDuration(preflight.analysisDurationSec)}
          detail={analysis.max_seconds > 0 ? `max_seconds ${analysis.max_seconds}` : 'max_seconds 0 = full'}
        />
        <Metric
          label='Chunking'
          value={analysis.chunked ? `${preflight.chunkCount} chunks` : 'Single pass'}
          detail={analysis.chunked ? `${analysis.chunk_duration_sec}s + ${analysis.chunk_overlap_sec}s overlap` : 'bez resume per chunk'}
        />
        <Metric
          label='YOLO frames'
          value={formatInteger(preflight.totalEstimatedYoloFrames)}
          detail={`players ${formatInteger(preflight.estimatedPlayerFrames)}${analysis.include_ball ? ` + ball ${formatInteger(preflight.estimatedBallFrames)}` : ''}`}
        />
        <Metric
          label='Modele'
          value={analysis.include_ball ? 'players + ball' : 'players'}
          detail={`${preflight.yoloModelPasses} pass${preflight.yoloModelPasses > 1 ? 'es' : ''}, device ${runtimeDevice}`}
        />
        <Metric
          label='Czas'
          value={preflight.estimatedWallTimeSec ? formatDuration(preflight.estimatedWallTimeSec) : 'po benchmarku'}
          detail={preflight.estimatedWallTimeSec ? preflight.estimateSource : 'najpierw odpal szybki test 3 min'}
        />
      </div>

      <div className='preflight-checks'>
        {preflight.checks.map((check) => (
          <div key={`${check.level}-${check.label}-${check.detail}`} className={`preflight-check ${check.level}`}>
            <strong>{levelLabels[check.level]} · {check.label}</strong>
            <span>{check.detail}</span>
          </div>
        ))}
      </div>

      <p className='muted preflight-storage-note'>{storageNote}</p>
    </div>
  );
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className='preflight-metric'>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function formatResolution(width: number, height: number): string {
  if (width <= 0 || height <= 0) return 'unknown';
  return `${width}x${height}`;
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const restSeconds = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${String(restSeconds).padStart(2, '0')}s`;
  if (minutes > 0) return `${minutes}m ${String(restSeconds).padStart(2, '0')}s`;
  return `${restSeconds}s`;
}

function formatInteger(value: number): string {
  return Math.max(0, Math.round(value)).toLocaleString('pl-PL');
}

function formatNumber(value: number, digits: number): string {
  return value.toLocaleString('pl-PL', {
    maximumFractionDigits: digits,
    minimumFractionDigits: value % 1 === 0 ? 0 : Math.min(1, digits),
  });
}
