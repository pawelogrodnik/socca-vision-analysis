import type { AnalysisPayload, Match, RuntimeInfo } from '../types';

export type AnalysisPresetId =
  | 'fast_debug'
  | 'standard_full'
  | 'production_ball'
  | 'quality_full';

export type AnalysisPreset = {
  id: AnalysisPresetId;
  label: string;
  description: string;
  patch: Partial<AnalysisPayload>;
};

export type PreflightCheckLevel = 'ok' | 'info' | 'warning' | 'blocking';

export type PreflightCheck = {
  level: PreflightCheckLevel;
  label: string;
  detail: string;
};

export type PlannedAnalysisChunk = {
  index: number;
  startTimeSec: number;
  endTimeSec: number;
  durationSec: number;
};

export type AnalysisPreflight = {
  videoDurationSec: number;
  videoFrameCount: number;
  videoFps: number;
  videoWidth: number;
  videoHeight: number;
  analysisDurationSec: number;
  analyzesFullVideo: boolean;
  plannedChunks: PlannedAnalysisChunk[];
  chunkCount: number;
  processedVideoSecIncludingOverlap: number;
  estimatedPlayerFrames: number;
  estimatedBallFrames: number;
  totalEstimatedYoloFrames: number;
  yoloModelPasses: number;
  estimatedWallTimeSec: number | null;
  estimateSource: string;
  checks: PreflightCheck[];
  canStart: boolean;
};

export const ANALYSIS_PRESETS: AnalysisPreset[] = [
  {
    id: 'fast_debug',
    label: 'Szybki test',
    description: '3 minuty, co druga klatka. Do sprawdzenia kalibracji i overlayu.',
    patch: {
      adapter: 'yolo',
      max_seconds: 180,
      frame_stride: 2,
      chunked: true,
      chunk_duration_sec: 45,
      chunk_overlap_sec: 2,
      include_ball: false,
      yolo_imgsz: 960,
      yolo_conf: 0.05,
      yolo_tracker: 'centroid_high_recall',
      camera_motion_compensation: true,
      camera_motion_interval_sec: 0.5,
      camera_motion_min_inlier_ratio: 0.6,
    },
  },
  {
    id: 'standard_full',
    label: 'Pelny mecz',
    description: 'Pelne video, tracking zawodnikow, bez pilki. Najbezpieczniejszy start.',
    patch: {
      adapter: 'yolo',
      max_seconds: 0,
      frame_stride: 1,
      chunked: true,
      chunk_duration_sec: 120,
      chunk_overlap_sec: 2,
      include_ball: false,
      yolo_imgsz: 1280,
      yolo_conf: 0.05,
      yolo_tracker: 'centroid_high_recall',
      camera_motion_compensation: true,
      camera_motion_interval_sec: 0.5,
      camera_motion_min_inlier_ratio: 0.6,
    },
  },
  {
    id: 'production_ball',
    label: 'Pelny + pilka',
    description: 'Pelne video, zawodnicy i pilka w jednym chunked jobie.',
    patch: {
      adapter: 'yolo',
      max_seconds: 0,
      frame_stride: 1,
      chunked: true,
      chunk_duration_sec: 120,
      chunk_overlap_sec: 2,
      include_ball: true,
      yolo_imgsz: 1280,
      yolo_conf: 0.05,
      yolo_tracker: 'centroid_high_recall',
      ball_yolo_model: 'models/best.pt',
      ball_yolo_conf: 0.03,
      ball_yolo_imgsz: 960,
      camera_motion_compensation: true,
      camera_motion_interval_sec: 0.5,
      camera_motion_min_inlier_ratio: 0.6,
    },
  },
  {
    id: 'quality_full',
    label: 'Jakosc',
    description: 'Ciezszy preset do dobrego GPU albo krotkiego porownania jakosci.',
    patch: {
      adapter: 'yolo',
      max_seconds: 0,
      frame_stride: 1,
      chunked: true,
      chunk_duration_sec: 90,
      chunk_overlap_sec: 3,
      include_ball: true,
      yolo_imgsz: 1920,
      yolo_conf: 0.05,
      yolo_tracker: 'centroid_high_recall',
      ball_yolo_model: 'models/best.pt',
      ball_yolo_conf: 0.03,
      ball_yolo_imgsz: 1280,
      camera_motion_compensation: true,
      camera_motion_interval_sec: 0.5,
      camera_motion_min_inlier_ratio: 0.6,
    },
  },
];

export function applyAnalysisPreset(
  current: AnalysisPayload,
  presetId: AnalysisPresetId,
  runtimeInfo?: RuntimeInfo | null,
): AnalysisPayload {
  const preset = ANALYSIS_PRESETS.find((candidate) => candidate.id === presetId);
  if (!preset) return current;
  const recommendedDevice = preferredAcceleratedDevice(runtimeInfo);
  return {
    ...current,
    ...preset.patch,
    yolo_device: recommendedDevice,
    ball_yolo_device: recommendedDevice,
  };
}

export function buildAnalysisPreflight(
  match: Match,
  analysis: AnalysisPayload,
  runtimeInfo: RuntimeInfo | null,
  options: {
    hasSavedPitchConfig: boolean;
    hasPendingPitchPoints: boolean;
  },
): AnalysisPreflight {
  const video = match.video;
  const videoFps = positiveNumber(video.fps);
  const videoDurationSec = positiveNumber(video.duration_sec);
  const videoFrameCount = Math.max(0, Math.round(Number(video.frame_count) || 0));
  const videoWidth = Math.max(0, Math.round(Number(video.width) || 0));
  const videoHeight = Math.max(0, Math.round(Number(video.height) || 0));
  const maxSeconds = Number(analysis.max_seconds) || 0;
  const analysisDurationSec = videoDurationSec > 0
    ? maxSeconds <= 0
      ? videoDurationSec
      : Math.min(videoDurationSec, Math.max(0, maxSeconds))
    : 0;
  const plannedChunks = analysis.chunked
    ? planAnalysisChunks(analysisDurationSec, analysis)
    : [
        {
          index: 1,
          startTimeSec: 0,
          endTimeSec: analysisDurationSec,
          durationSec: analysisDurationSec,
        },
      ];
  const processedVideoSecIncludingOverlap = plannedChunks.reduce(
    (sum, chunk) => sum + chunk.durationSec,
    0,
  );
  const frameStride = Math.max(1, Math.round(Number(analysis.frame_stride) || 1));
  const estimatedPlayerFrames = Math.ceil((processedVideoSecIncludingOverlap * videoFps) / frameStride);
  const estimatedBallFrames = analysis.include_ball ? estimatedPlayerFrames : 0;
  const timeEstimate = estimateWallTimeSec(match, analysis, processedVideoSecIncludingOverlap);
  const checks = buildPreflightChecks({
    analysis,
    analysisDurationSec,
    estimatedBallFrames,
    estimatedPlayerFrames,
    match,
    options,
    plannedChunks,
    runtimeInfo,
    videoDurationSec,
    videoFps,
    videoHeight,
    videoWidth,
  });
  return {
    videoDurationSec,
    videoFrameCount,
    videoFps,
    videoWidth,
    videoHeight,
    analysisDurationSec,
    analyzesFullVideo: maxSeconds <= 0 || analysisDurationSec >= videoDurationSec - 0.001,
    plannedChunks,
    chunkCount: plannedChunks.length,
    processedVideoSecIncludingOverlap,
    estimatedPlayerFrames,
    estimatedBallFrames,
    totalEstimatedYoloFrames: estimatedPlayerFrames + estimatedBallFrames,
    yoloModelPasses: analysis.include_ball ? 2 : 1,
    estimatedWallTimeSec: timeEstimate.seconds,
    estimateSource: timeEstimate.source,
    checks,
    canStart: !checks.some((check) => check.level === 'blocking'),
  };
}

function buildPreflightChecks(input: {
  analysis: AnalysisPayload;
  analysisDurationSec: number;
  estimatedBallFrames: number;
  estimatedPlayerFrames: number;
  match: Match;
  options: {
    hasSavedPitchConfig: boolean;
    hasPendingPitchPoints: boolean;
  };
  plannedChunks: PlannedAnalysisChunk[];
  runtimeInfo: RuntimeInfo | null;
  videoDurationSec: number;
  videoFps: number;
  videoHeight: number;
  videoWidth: number;
}): PreflightCheck[] {
  const checks: PreflightCheck[] = [];
  const hasAnyPitch = input.options.hasSavedPitchConfig || input.options.hasPendingPitchPoints;
  if (!hasAnyPitch) {
    checks.push({
      level: 'blocking',
      label: 'Boisko',
      detail: 'Brakuje 4 punktow boiska. Analiza nie powinna startowac bez pitch_config.',
    });
  } else if (input.options.hasSavedPitchConfig) {
    checks.push({
      level: 'ok',
      label: 'Boisko',
      detail: 'Pitch config jest zapisany i bedzie uzyty przez analize.',
    });
  } else {
    checks.push({
      level: 'info',
      label: 'Boisko',
      detail: 'Masz 4 punkty na klatce. Zostana zapisane tuz przed startem analizy.',
    });
  }

  if (input.videoDurationSec <= 0 || input.videoFps <= 0) {
    checks.push({
      level: 'warning',
      label: 'Metadata video',
      detail: 'Brakuje pelnych metadanych video, wiec preflight moze miec niedokladne szacunki.',
    });
  }

  if (input.analysis.max_seconds > 0 && input.analysisDurationSec < input.videoDurationSec - 0.001) {
    checks.push({
      level: 'warning',
      label: 'Zakres analizy',
      detail: `Analiza obejmie tylko pierwsze ${formatDuration(input.analysisDurationSec)}, a nie caly film.`,
    });
  } else {
    checks.push({
      level: 'ok',
      label: 'Zakres analizy',
      detail: 'Preset obejmuje caly dostepny material video.',
    });
  }

  if (input.analysisDurationSec > 300 && !input.analysis.chunked) {
    checks.push({
      level: 'warning',
      label: 'Chunking',
      detail: 'Dla dlugiego filmu wlacz chunked mode, zeby miec retry/resume po awarii.',
    });
  } else if (input.analysis.chunked) {
    checks.push({
      level: 'ok',
      label: 'Chunking',
      detail: `Zaplanowano ${input.plannedChunks.length} chunkow z retry/resume.`,
    });
  }

  if (input.analysis.frame_stride > 1) {
    checks.push({
      level: 'warning',
      label: 'Frame stride',
      detail: 'Stride > 1 przyspiesza test, ale pogarsza statystyki dystansu i predkosci.',
    });
  }

  if (input.analysis.include_ball) {
    checks.push({
      level: 'info',
      label: 'Pilka',
      detail: `Ball model doda ok. ${formatInteger(input.estimatedBallFrames)} dodatkowych ramek YOLO w tym samym jobie.`,
    });
    if (!input.analysis.ball_yolo_model.trim()) {
      checks.push({
        level: 'blocking',
        label: 'Ball model',
        detail: 'Wlaczona analiza pilki wymaga sciezki do modelu ball YOLO.',
      });
    }
  }

  const accelerated = preferredAcceleratedDevice(input.runtimeInfo);
  const requestedDevices = selectedAnalysisDevices(input.analysis);
  const unavailableAccelerator = requestedDevices.find((device) =>
    (isCudaDevice(device) && !input.runtimeInfo?.torch.cuda_available) ||
    (device === 'mps' && !input.runtimeInfo?.torch.mps_available)
  );
  if (unavailableAccelerator) {
    checks.push({
      level: 'blocking',
      label: 'Runtime device',
      detail: `Wybrano device=${unavailableAccelerator}, ale backend nie raportuje takiego akceleratora. Uruchom native CUDA backend albo wybierz CPU/Auto.`,
    });
  }
  if (accelerated) {
    const gpuName = accelerated === '0'
      ? input.runtimeInfo?.torch.active_cuda_device_name || input.runtimeInfo?.torch.cuda_device_names?.[0]
      : null;
    checks.push({
      level: 'ok',
      label: 'Runtime',
      detail: `Backend widzi akcelerator YOLO: ${gpuName ? `${accelerated} (${gpuName})` : accelerated}.`,
    });
  } else {
    checks.push({
      level: 'warning',
      label: 'Runtime',
      detail: 'Backend nie raportuje CUDA/MPS. Pelny mecz na CPU moze trwac bardzo dlugo.',
    });
  }

  if (input.videoWidth >= 3800 || input.videoHeight >= 2100) {
    checks.push({
      level: 'warning',
      label: '4K',
      detail: 'To wyglada na 4K. Rozwaz preset standard/1280 przed ciezkim presetem jakosci.',
    });
  }

  if (input.estimatedPlayerFrames > 90000) {
    checks.push({
      level: 'info',
      label: 'Skala runa',
      detail: `Player tracker przetworzy ok. ${formatInteger(input.estimatedPlayerFrames)} ramek przed finalnym merge.`,
    });
  }

  if (!latestPerformanceReport(input.match)) {
    checks.push({
      level: 'info',
      label: 'Benchmark',
      detail: 'Brak performance_report. Odpal szybki test 3 min, zeby preflight policzyl realny czas pelnego runa.',
    });
  }

  if ((input.match.teams || []).length === 0) {
    checks.push({
      level: 'info',
      label: 'Roster',
      detail: 'Roster nie blokuje analizy. Realnych zawodnikow mozesz przypisac po overlay review.',
    });
  }

  return checks;
}

function estimateWallTimeSec(
  match: Match,
  analysis: AnalysisPayload,
  processedVideoSecIncludingOverlap: number,
): { seconds: number | null; source: string } {
  const report = latestPerformanceReport(match);
  const throughput = Number(report?.throughput?.video_seconds_per_wall_second || 0);
  if (!report || throughput <= 0) {
    return { seconds: null, source: 'brak benchmarku' };
  }
  const previousParameters = report.parameters || {};
  const previousStride = Math.max(1, Number(previousParameters.frame_stride) || Math.max(1, analysis.frame_stride));
  const currentStride = Math.max(1, Number(analysis.frame_stride) || 1);
  const previousImgSize = Math.max(320, Number(previousParameters.yolo_imgsz) || analysis.yolo_imgsz || 960);
  const currentImgSize = Math.max(320, Number(analysis.yolo_imgsz) || 960);
  const previousIncludedBall = Boolean(previousParameters.include_ball);
  let multiplier = previousStride / currentStride;
  multiplier *= Math.pow(currentImgSize / previousImgSize, 1.6);
  if (analysis.include_ball && !previousIncludedBall) multiplier *= 1.8;
  if (!analysis.include_ball && previousIncludedBall) multiplier *= 0.65;
  const estimated = (processedVideoSecIncludingOverlap / throughput) * Math.max(0.1, multiplier);
  return {
    seconds: Number.isFinite(estimated) ? estimated : null,
    source: `z ${report.label || 'performance_report.json'}`,
  };
}

function latestPerformanceReport(match: Match) {
  return match.analysis_report?.performance_report || match.performance_report || null;
}

function planAnalysisChunks(
  analysisDurationSec: number,
  analysis: AnalysisPayload,
): PlannedAnalysisChunk[] {
  const chunks: PlannedAnalysisChunk[] = [];
  if (analysisDurationSec <= 0) return chunks;
  const chunkDurationSec = Math.max(1, Number(analysis.chunk_duration_sec) || 120);
  const overlapSec = Math.max(
    0,
    Math.min(Number(analysis.chunk_overlap_sec) || 0, chunkDurationSec / 2),
  );
  let start = 0;
  let index = 1;
  while (start < analysisDurationSec - 0.000001) {
    const end = Math.min(analysisDurationSec, start + chunkDurationSec);
    chunks.push({
      index,
      startTimeSec: roundSeconds(start),
      endTimeSec: roundSeconds(end),
      durationSec: roundSeconds(Math.max(0, end - start)),
    });
    if (end >= analysisDurationSec) break;
    start = Math.max(0, end - overlapSec);
    index += 1;
  }
  return chunks;
}

function preferredAcceleratedDevice(runtimeInfo?: RuntimeInfo | null): string | null {
  const devices = runtimeInfo?.recommended_yolo_devices || [];
  return devices.find((device) => device !== 'cpu') || null;
}

function selectedAnalysisDevices(analysis: AnalysisPayload): string[] {
  const devices = new Set<string>();
  const playerDevice = normalizeDeviceValue(analysis.yolo_device);
  const ballDevice = normalizeDeviceValue(analysis.ball_yolo_device || analysis.yolo_device);
  if (playerDevice) devices.add(playerDevice);
  if (analysis.include_ball && ballDevice) devices.add(ballDevice);
  return Array.from(devices);
}

function normalizeDeviceValue(device: string | null | undefined): string | null {
  const raw = String(device || '').trim().toLowerCase();
  if (!raw || raw === 'auto' || raw === 'default' || raw === 'none' || raw === 'null') return null;
  if (raw === 'cuda' || raw === 'gpu' || raw === 'nvidia') return '0';
  if (raw.startsWith('cuda:')) return raw.slice('cuda:'.length);
  return raw;
}

function isCudaDevice(device: string): boolean {
  return /^\d+$/.test(device);
}

function positiveNumber(value: unknown): number {
  const numeric = Number(value) || 0;
  return numeric > 0 ? numeric : 0;
}

function roundSeconds(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function formatInteger(value: number): string {
  return Math.max(0, Math.round(value)).toLocaleString('pl-PL');
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const restSeconds = total % 60;
  if (minutes <= 0) return `${restSeconds}s`;
  return `${minutes}m ${String(restSeconds).padStart(2, '0')}s`;
}
