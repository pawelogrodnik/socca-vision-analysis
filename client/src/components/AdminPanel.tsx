import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  analyzeBall,
  artifactUrl,
  createMatchPackage,
  frameUrl,
  getAnalysisJob,
  getMatch,
  getReviewWorkflow,
  getRuntimeInfo,
  listMatches,
  publishLocalMatch,
  savePitch,
  startAnalysisJob,
  updateMatchMetadata,
} from '../api';
import type {
  AnalysisJob,
  AnalysisPayload,
  BallAnalysisPayload,
  Match,
  MatchMetadataPayload,
  RuntimeInfo,
  ReviewWorkflow,
} from '../types';
import { applyAnalysisPreset, preferredAcceleratedDevice, type AnalysisPresetId } from '../lib/analysisPreflight';
import { DEFAULT_BALL_YOLO_MODEL, DEFAULT_PLAYER_YOLO_MODEL } from '../lib/modelDefaults';
import { drawPitchOverlay, errorMessage } from '../lib/helpers';
import {
  suggestedTopLevelStep,
  topLevelStepStatus,
  type TopLevelWorkflowStepId,
} from '../utils/adminWorkflowNavigation';
import {
  reportWorkflowOperatorCopy,
  reviewWorkflowOperatorCopy,
} from '../utils/reviewWorkflowPresentation';
import { AnalysisArtifacts } from './AnalysisArtifacts';
import { AnalysisForm } from './AnalysisForm';
import { AnalysisPreflightPanel } from './AnalysisPreflightPanel';
import { AnalysisQualityPanel } from './AnalysisQualityPanel';
import { IdentityCandidatePanel } from '../IdentityCandidatePanel';
import { IdentityReviewWorkspace } from './IdentityReviewWorkspace';
import { IdentityCropReviewPanel } from './IdentityCropReviewPanel';
import { IdentityRosterSubjectReviewPanel } from './IdentityRosterSubjectReviewPanel';
import { MatchList } from './MatchList';
import { MatchRosterPanel } from './MatchRosterPanel';
import { MatchSummary } from './MatchSummary';
import {
  MatchWorkflowStepper,
  type WorkflowStep,
} from './MatchWorkflowStepper';
import { MetadataEditor } from './MetadataEditor';
import { NewMatchForm } from './NewMatchForm';
import { PublishedDatabasePanel } from './PublishedDatabasePanel';
import { StablePlayersPanel } from './StablePlayersPanel';
import { SecondHalfIdentityReanchorPanel } from './SecondHalfIdentityReanchorPanel';
import { TeamConfigPanel } from './TeamConfigPanel';
import { TrackletAssignmentPanel } from './TrackletAssignmentPanel';

const defaultAnalysis: AnalysisPayload = {
  adapter: 'yolo',
  max_seconds: 0,
  frame_stride: 1,
  chunked: true,
  chunk_duration_sec: 120,
  chunk_overlap_sec: 2,
  include_ball: true,
  render_stable_overlay: true,
  yolo_model: DEFAULT_PLAYER_YOLO_MODEL,
  yolo_conf: 0.05,
  yolo_imgsz: 1280,
  yolo_tracker: 'centroid_high_recall',
  yolo_device: null,
  ball_yolo_model: DEFAULT_BALL_YOLO_MODEL,
  ball_yolo_conf: 0.03,
  ball_yolo_imgsz: 960,
  ball_yolo_device: null,
  camera_motion_compensation: true,
  camera_motion_interval_sec: 0.5,
  camera_motion_min_inlier_ratio: 0.6,
};

const defaultBallAnalysis: BallAnalysisPayload = {
  max_seconds: 3,
  frame_stride: 4,
  yolo_model: DEFAULT_BALL_YOLO_MODEL,
  yolo_conf: 0.05,
  yolo_imgsz: 960,
  yolo_device: null,
};

type Point = [number, number];
type WorkflowStepId = TopLevelWorkflowStepId;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

const terminalAnalysisStatuses = new Set(['completed', 'failed', 'interrupted', 'cancelled']);

const workflowCopy: Record<
  WorkflowStepId,
  { label: string; description: string }
> = {
  video: {
    label: 'Wideo',
    description: 'dodaj lub wybierz mecz',
  },
  analysis: {
    label: 'Analiza',
    description: 'boisko + YOLO',
  },
  review: {
    label: 'Review',
    description: 'zawodnicy i jakosc',
  },
  publish: {
    label: 'Raport',
    description: 'raport i publikacja',
  },
};

function isAnalysisCompleted(match: Match | null): boolean {
  return match?.analysis_report?.status === 'completed';
}

function hasPitchConfig(match: Match | null): boolean {
  return Boolean(match?.pitch_config);
}

function isTerminalAnalysisStatus(status: string): boolean {
  return terminalAnalysisStatuses.has(status);
}

function formatElapsed(seconds?: number): string | null {
  if (seconds === undefined || seconds === null || !Number.isFinite(seconds)) return null;
  const rounded = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  if (minutes <= 0) return `${rest}s`;
  return `${minutes}m ${rest.toString().padStart(2, '0')}s`;
}

function formatRelativeAge(iso?: string | null): string | null {
  if (!iso) return null;
  const timestamp = Date.parse(iso);
  if (!Number.isFinite(timestamp)) return null;
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds}s temu`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}m ${rest.toString().padStart(2, '0')}s temu`;
}

function progressCurrentText(job: AnalysisJob): string | null {
  const current = job.progress_plan?.current;
  if (!current) return null;
  const unit = current.unit || 'items';
  if (current.current !== undefined && current.total !== undefined) {
    return `${current.current}/${current.total} ${unit}`;
  }
  if (current.total !== undefined) {
    return `${current.total} ${unit}`;
  }
  return current.label || null;
}

function activeProgressStep(job: AnalysisJob) {
  const activeStepId = job.progress_plan?.active_step_id;
  return job.progress_plan?.steps?.find((step) => step.id === activeStepId) || null;
}

function isJobHeartbeatStale(job: AnalysisJob): boolean {
  if (isTerminalAnalysisStatus(job.status)) return false;
  const heartbeat = job.progress_plan?.last_heartbeat_at || job.updated_at;
  const timestamp = Date.parse(heartbeat || '');
  if (!Number.isFinite(timestamp)) return false;
  return Date.now() - timestamp > 90_000;
}

function workflowSteps(
  activeStep: WorkflowStepId,
  selected: Match | null,
  workflow: ReviewWorkflow | null,
): WorkflowStep[] {
  return (Object.keys(workflowCopy) as WorkflowStepId[]).map((stepId) => {
    const status = topLevelStepStatus(stepId, activeStep, selected, workflow);
    const description = stepId === 'review'
      ? reviewWorkflowOperatorCopy(workflow)
      : stepId === 'publish'
        ? reportWorkflowOperatorCopy(workflow)
        : workflowCopy[stepId].description;
    return {
      id: stepId,
      label: workflowCopy[stepId].label,
      description,
      status,
      statusLabel: stepId === 'review' || stepId === 'publish' ? description : undefined,
      disabled: status === 'locked',
    };
  });
}

function pointsFromPitchConfig(config: unknown): Point[] {
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    return [];
  }
  const points = (config as { image_points?: unknown }).image_points;
  if (!Array.isArray(points) || points.length !== 4) return [];
  const parsed = points
    .map((point) => {
      if (!Array.isArray(point) || point.length < 2) return null;
      const x = Number(point[0]);
      const y = Number(point[1]);
      return Number.isFinite(x) && Number.isFinite(y) ? ([x, y] as Point) : null;
    })
    .filter((point): point is Point => Boolean(point));
  return parsed.length === 4 ? parsed : [];
}

export function AdminPanel() {
  const [matches, setMatches] = useState<Match[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState<Match | null>(null);
  const [activeStep, setActiveStep] = useState<WorkflowStepId>('video');
  const [status, setStatus] = useState('');
  const [analysis, setAnalysis] = useState<AnalysisPayload>(defaultAnalysis);
  const [ballAnalysis, setBallAnalysis] = useState<BallAnalysisPayload>(defaultBallAnalysis);
  const [frameSecond, setFrameSecond] = useState(1);
  const [frameSrc, setFrameSrc] = useState('');
  const [pitchPoints, setPitchPoints] = useState<Point[]>([]);
  const [showDeveloperDebug, setShowDeveloperDebug] = useState(false);
  const [showDeveloperDiagnostics, setShowDeveloperDiagnostics] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [activeAnalysisJob, setActiveAnalysisJob] = useState<AnalysisJob | null>(null);
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeInfo | null>(null);
  const [reviewWorkflow, setReviewWorkflow] = useState<ReviewWorkflow | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const isBusy = busyAction !== null;

  async function refresh(selectId?: string, nextStep?: WorkflowStepId) {
    const items = await listMatches();
    setMatches(items);
    const nextId = selectId || selectedId || items[0]?.id || '';
    setSelectedId(nextId);
    if (!nextId) {
      setSelected(null);
      setReviewWorkflow(null);
      setActiveStep('video');
      return;
    }
    const [nextMatch, nextWorkflow] = await Promise.all([getMatch(nextId), getReviewWorkflow(nextId)]);
    setSelected(nextMatch);
    setReviewWorkflow(nextWorkflow);
    setPitchPoints(pointsFromPitchConfig(nextMatch.pitch_config));
    setActiveStep(nextStep || suggestedTopLevelStep(nextMatch, nextWorkflow));
  }

  useEffect(() => {
    refresh().catch((error) => setStatus(errorMessage(error)));
    getRuntimeInfo()
      .then(setRuntimeInfo)
      .catch(() => setRuntimeInfo(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const recommendedDevice = preferredAcceleratedDevice(runtimeInfo);
    if (!recommendedDevice) return;
    setAnalysis((current) => {
      if (current.yolo_device || current.ball_yolo_device) return current;
      return {
        ...current,
        yolo_device: recommendedDevice,
        ball_yolo_device: recommendedDevice,
      };
    });
    setBallAnalysis((current) => {
      if (current.yolo_device) return current;
      return {
        ...current,
        yolo_device: recommendedDevice,
      };
    });
  }, [runtimeInfo]);

  useEffect(() => {
    if (!selectedId) return;
    Promise.all([getMatch(selectedId), getReviewWorkflow(selectedId)])
      .then(([match, workflow]) => {
        setSelected(match);
        setReviewWorkflow(workflow);
        setPitchPoints(pointsFromPitchConfig(match.pitch_config));
        setActiveStep(suggestedTopLevelStep(match, workflow));
      })
      .catch((error) => setStatus(errorMessage(error)));
  }, [selectedId]);

  useEffect(() => {
    if (activeStep !== 'analysis' || !selectedId) return;
    setFrameSrc(frameUrl(selectedId, frameSecond));
  }, [activeStep, selectedId]);

  useEffect(() => {
    if (
      activeStep === 'publish'
      && isAnalysisCompleted(selected)
      && reviewWorkflow?.can_enter_report !== true
    ) {
      setActiveStep('review');
    }
  }, [activeStep, reviewWorkflow?.can_enter_report, selected]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !frameSrc) return;
    const img = new Image();
    img.onload = () => {
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.drawImage(img, 0, 0);
      drawPitchOverlay(ctx, pitchPoints);
    };
    img.src = frameSrc;
  }, [frameSrc, pitchPoints]);

  async function handleCreated(match: Match) {
    setStatus(`Dodano mecz: ${match.title}`);
    await refresh(match.id, 'analysis');
  }

  async function selectMatch(matchId: string) {
    setSelectedId(matchId);
  }

  async function refreshSelected(nextStep?: WorkflowStepId) {
    if (!selectedId) return;
    const [match, workflow] = await Promise.all([getMatch(selectedId), getReviewWorkflow(selectedId)]);
    setSelected(match);
    setReviewWorkflow(workflow);
    setPitchPoints(pointsFromPitchConfig(match.pitch_config));
    if (nextStep) setActiveStep(nextStep);
  }

  async function loadFrame() {
    if (!selectedId) return;
    setFrameSrc(frameUrl(selectedId, frameSecond));
    setPitchPoints(pointsFromPitchConfig(selected?.pitch_config));
  }

  function handleCanvasClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas || pitchPoints.length >= 4) return;
    const rect = canvas.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * canvas.width;
    const y = ((event.clientY - rect.top) / rect.height) * canvas.height;
    setPitchPoints((points) => [...points, [x, y]]);
  }

  async function savePitchOnly() {
    if (isBusy) return;
    if (!selectedId || pitchPoints.length !== 4) {
      setStatus('Kliknij dokladnie 4 rogi boiska.');
      return;
    }
    setBusyAction('pitch');
    setStatus('Zapisuje konfiguracje boiska...');
    try {
      await savePitch(selectedId, {
        image_points: pitchPoints,
        width_m: 30,
        length_m: 47.4,
        pitch_dimensions_m: { width_m: 30, length_m: 47.4 },
        calibration_frame_time_sec: frameSecond,
        source: 'manual',
      });
      setStatus('Zapisano konfiguracje boiska 30 x 47.4 m.');
      await refreshSelected('analysis');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function runAnalysis() {
    if (!selectedId || isBusy) return;
    if (!hasPitchConfig(selected) && pitchPoints.length !== 4) {
      setStatus('Najpierw kliknij 4 rogi boiska albo zapisz konfiguracje boiska.');
      return;
    }
    setBusyAction('analysis');
    setStatus('Uruchamiam analize w tle...');
    try {
      const job = await startAnalysisJob(selectedId, analysis);
      setActiveAnalysisJob(job);
      await waitForAnalysisJob(job.job_id);
      setStatus('Analiza zakończona. Przejdź do Review zawodników.');
      await refreshSelected('review');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  function handleApplyAnalysisPreset(presetId: AnalysisPresetId) {
    setAnalysis((current) => applyAnalysisPreset(current, presetId, runtimeInfo));
    setStatus('Zastosowano preset analizy. Sprawdz production preflight przed startem.');
  }

  async function runBallAnalysis() {
    if (!selectedId || isBusy) return;
    if (!hasPitchConfig(selected)) {
      setStatus('Najpierw zapisz konfiguracje boiska.');
      return;
    }
    setBusyAction('ball-analysis');
    setStatus('Uruchamiam szybki test detekcji pilki...');
    try {
      const report = await analyzeBall(selectedId, {
        ...ballAnalysis,
        frame_stride: Math.max(1, ballAnalysis.frame_stride),
        yolo_model: ballAnalysis.yolo_model || analysis.yolo_model,
        yolo_device: ballAnalysis.yolo_device || analysis.yolo_device,
      });
      const summary = report.ball_tracking_summary as
        | { known_coverage?: number; candidate_count?: number }
        | undefined;
      const recommendation = report.ball_quality_recommendation;
      const coverage = Number(summary?.known_coverage ?? 0) * 100;
      const candidates = Number(summary?.candidate_count ?? 0);
      setStatus(
        `Test pilki zakonczony: known coverage ${coverage.toFixed(1)}%, kandydaci ${candidates}, decyzja ${recommendation?.decision || 'n/a'}.`,
      );
      await refreshSelected('review');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function savePitchAndRunAnalysis() {
    if (!selectedId || isBusy) return;
    const canUseExistingPitch = hasPitchConfig(selected);
    if (!canUseExistingPitch && pitchPoints.length !== 4) {
      setStatus('Kliknij 4 rogi boiska przed analiza.');
      return;
    }
    setBusyAction('analysis');
    setStatus('Zapisuje boisko i uruchamiam analize w tle...');
    try {
      if (pitchPoints.length === 4) {
        await savePitch(selectedId, {
          image_points: pitchPoints,
          width_m: 30,
          length_m: 47.4,
          pitch_dimensions_m: { width_m: 30, length_m: 47.4 },
          calibration_frame_time_sec: frameSecond,
          source: 'manual',
        });
      }
      const job = await startAnalysisJob(selectedId, analysis);
      setActiveAnalysisJob(job);
      await waitForAnalysisJob(job.job_id);
      setStatus('Analiza zakończona. Przejdź do Review zawodników.');
      await refreshSelected('review');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function waitForAnalysisJob(jobId: string): Promise<AnalysisJob> {
    let latest = await fetchAnalysisJobWithRetry(jobId);
    setActiveAnalysisJob(latest);
    while (!isTerminalAnalysisStatus(latest.status)) {
      const chunkText = latest.chunk_count ? ` chunks ${latest.chunk_count}` : '';
      const step = activeProgressStep(latest);
      const stepText = step ? ` etap: ${step.label}` : ` etap: ${latest.stage}`;
      const currentText = progressCurrentText(latest);
      const progressText = currentText ? ` (${currentText})` : '';
      setStatus(
        `Analiza ${latest.status}:${stepText} ${Math.round(latest.progress_percent || 0)}%${progressText}${chunkText}. ${latest.message || ''}`,
      );
      await delay(2000);
      latest = await fetchAnalysisJobWithRetry(jobId);
      setActiveAnalysisJob(latest);
    }
    if (latest.status !== 'completed') {
      throw new Error(latest.error?.message || latest.message || `Analysis job ${latest.status}`);
    }
    return latest;
  }

  async function fetchAnalysisJobWithRetry(jobId: string): Promise<AnalysisJob> {
    let lastError: unknown;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        return await getAnalysisJob(jobId);
      } catch (error) {
        lastError = error;
        await delay(400 * (attempt + 1));
      }
    }
    throw lastError;
  }

  async function buildPackage() {
    if (!selectedId || isBusy) return;
    if (!reviewWorkflow?.can_publish) {
      setStatus('Najpierw zakończ Review, aby przygotować techniczną paczkę meczu.');
      return;
    }
    setBusyAction('package');
    setStatus('Generuje match_package.json...');
    try {
      await createMatchPackage(selectedId);
      setStatus('Wygenerowano match_package.json.');
      await refreshSelected('publish');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function publishSelected(replace = false) {
    if (!selectedId || isBusy) return;
    if (!reviewWorkflow?.can_publish) {
      setStatus('Najpierw zakończ Review i zatwierdź wideo.');
      return;
    }
    setBusyAction('publish');
    setStatus(replace ? 'Nadpisuje mecz w bazie...' : 'Importuje mecz do SQLite...');
    try {
      const published = await publishLocalMatch(selectedId, replace);
      setStatus(`<p>Mecz opublikowany w bazie jako ${published.id}. <a href="/published/matches/${published.id}/report">Link do raportu</a></p>`);
      await refreshSelected('publish');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function saveMetadata(payload: MatchMetadataPayload) {
    if (!selectedId || isBusy) return;
    setBusyAction('metadata');
    setStatus('Zapisuje metadane meczu...');
    try {
      const updated = await updateMatchMetadata(selectedId, payload);
      setSelected(updated);
      setStatus('Zapisano metadane meczu.');
      await refresh(updated.id, activeStep);
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function saveRosterTeams(
    teams: Match['teams'],
    identityReviewScope: NonNullable<Match['identity_review_scope']>,
  ) {
    if (!selectedId || !selected || isBusy) return;
    setBusyAction('metadata');
    setStatus('Zapisuje roster meczu...');
    try {
      const updated = await updateMatchMetadata(selectedId, {
        title: selected.title,
        match_date: selected.match_date || null,
        season: selected.season || null,
        venue: selected.venue || null,
        format: selected.format || '7v7',
        status: selected.status || 'uploaded',
        teams,
        identity_review_scope: identityReviewScope,
      });
      setSelected(updated);
      await refresh(updated.id, activeStep);
    } catch (error) {
      setStatus(errorMessage(error));
      throw error;
    } finally {
      setBusyAction(null);
    }
  }

  function selectStep(stepId: string) {
    const nextStep = stepId as WorkflowStepId;
    if (nextStep === 'analysis' && !selected) return;
    if ((nextStep === 'review' || nextStep === 'publish') && !isAnalysisCompleted(selected)) {
      return;
    }
    if (nextStep === 'publish' && !reviewWorkflow?.can_enter_report) {
      return;
    }
    setActiveStep(nextStep);
  }

  const savedPitchConfig = hasPitchConfig(selected);
  const canAnalyze = Boolean(selected && (pitchPoints.length === 4 || savedPitchConfig));
  const reviewStableOverlay = selected?.analysis_report?.artifacts?.stable_overlay_preview;
  const reviewStableOverlaySkipped = selected?.analysis_report?.parameters?.render_stable_overlay === false;
  const steps = workflowSteps(activeStep, selected, reviewWorkflow);
  const activeJobStep = activeAnalysisJob ? activeProgressStep(activeAnalysisJob) : null;
  const activeJobCurrent = activeAnalysisJob ? progressCurrentText(activeAnalysisJob) : null;
  const activeJobElapsed = activeAnalysisJob
    ? formatElapsed(activeAnalysisJob.progress_plan?.active_step_elapsed_sec)
    : null;
  const activeJobHeartbeat = activeAnalysisJob
    ? formatRelativeAge(activeAnalysisJob.progress_plan?.last_heartbeat_at || activeAnalysisJob.updated_at)
    : null;
  const activeJobStale = activeAnalysisJob ? isJobHeartbeatStale(activeAnalysisJob) : false;

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Local workflow</p>
        <h1>Dodawanie i analiza meczu</h1>
        <p>
          Wybierz video, skalibruj boisko, uruchom analize, przypisz swoich
          zawodnikow i opublikuj raport.
        </p>
        <div className='row'>
          <Link to='/'>Public viewer</Link>
          <Link to='/teams'>Rejestr druzyn</Link>
        </div>
      </section>

      {status && (
        <p className={isBusy ? 'status loading-status' : 'status'}>
          {isBusy && <span className='spinner' />}
          {status}
        </p>
      )}

      <MatchWorkflowStepper steps={steps} onSelect={selectStep} />

      {activeStep === 'video' && (
        <section className='card workflow-card'>
          <div className='row between'>
            <div>
              <h2>1. Dodaj lub wybierz video</h2>
              <p className='muted'>
                Metadata i roster sa opcjonalne. Najwazniejsze jest video,
                ktore przejdzie do kalibracji i analizy.
              </p>
            </div>
            {selected && (
              <button
                type='button'
                onClick={() => setActiveStep('analysis')}
                disabled={isBusy}
              >
                Przejdz do analizy
              </button>
            )}
          </div>
          <div className='grid two workflow-grid'>
            <div>
              <h3>Nowe video</h3>
              <NewMatchForm onCreated={handleCreated} onError={setStatus} />
            </div>
            <div>
              <h3>Istniejace mecze lokalne</h3>
              <MatchList
                matches={matches}
                selectedId={selectedId}
                onSelect={selectMatch}
              />
              {selected && <MatchSummary match={selected} />}
            </div>
          </div>
          {selected && (
            <MatchRosterPanel
              match={selected}
              disabled={isBusy}
              surface='panel'
              onSave={saveRosterTeams}
              onStatus={setStatus}
            />
          )}
          {selected && (
            <details className='debug-details'>
              <summary>Opcjonalnie: edytuj metadane wybranego meczu</summary>
              <MetadataEditor match={selected} onSave={saveMetadata} />
            </details>
          )}
        </section>
      )}

      {activeStep === 'analysis' && selected && (
        <section className='card workflow-card'>
          <div className='row between'>
            <div>
              <h2>2. Kalibracja boiska i analiza</h2>
              <p className='muted'>
                Kliknij 4 rogi boiska na jednej klatce. Ustawienia YOLO sa
                domyslne i schowane w zaawansowanych opcjach.
              </p>
            </div>
            <div className='row'>
              <button type='button' className='secondary' onClick={() => setActiveStep('video')}>
                Zmien video
              </button>
              <button
                type='button'
                onClick={savePitchAndRunAnalysis}
                disabled={isBusy || !canAnalyze}
              >
                {busyAction === 'analysis'
                  ? 'Analiza w toku...'
                  : pitchPoints.length === 4
                    ? 'Zapisz boisko i uruchom analize'
                    : 'Uruchom analize'}
              </button>
            </div>
          </div>
          <div className='controls'>
            <label>
              Sekunda klatki do kalibracji
              <input
                type='number'
                value={frameSecond}
                min={0}
                step={0.5}
                onChange={(event) => setFrameSecond(Number(event.target.value))}
              />
            </label>
            <div className='row'>
              <button type='button' onClick={loadFrame} disabled={isBusy}>
                Zaladuj klatke
              </button>
              <button
                type='button'
                className='secondary'
                onClick={() => setPitchPoints([])}
                disabled={isBusy}
              >
                Wyczysc punkty
              </button>
              <button type='button' onClick={savePitchOnly} disabled={isBusy || pitchPoints.length !== 4}>
                {busyAction === 'pitch' ? 'Zapisuje...' : 'Zapisz boisko'}
              </button>
            </div>
          </div>
          {activeAnalysisJob && (
            <div className='job-status-box'>
              <div className='row between'>
                <div>
                  <strong>Analysis job: {activeAnalysisJob.job_id}</strong>
                  <span>
                    {activeAnalysisJob.status} / {activeAnalysisJob.stage} /{' '}
                    {Math.round(activeAnalysisJob.progress_percent || 0)}%
                  </span>
                </div>
                {activeAnalysisJob.chunk_manifest && selectedId && (
                  <a href={artifactUrl(selectedId, activeAnalysisJob.chunk_manifest)}>
                    chunk manifest
                  </a>
                )}
              </div>
              <p className='muted'>{activeAnalysisJob.message}</p>
              {activeJobStep && (
                <div className='job-progress-summary'>
                  <span>Aktywny etap: {activeJobStep.label}</span>
                  {activeJobCurrent && <span>Postep etapu: {activeJobCurrent}</span>}
                  {activeJobElapsed && <span>Czas etapu: {activeJobElapsed}</span>}
                  {activeJobHeartbeat && <span>Heartbeat: {activeJobHeartbeat}</span>}
                </div>
              )}
              {activeJobStale && (
                <p className='job-stale-warning'>
                  Brak nowego heartbeatu od ponad 90 sekund. Proces moze nadal renderowac
                  ciezki artefakt, ale ten etap wymaga sprawdzenia w logach backendu.
                </p>
              )}
              {activeAnalysisJob.progress_plan?.steps && (
                <ol className='job-progress-plan'>
                  {activeAnalysisJob.progress_plan.steps.map((step) => (
                    <li
                      key={step.id}
                      className={`job-progress-step ${step.status}`}
                    >
                      <span className='job-progress-marker' />
                      <span>{step.label}</span>
                      {step.status === 'running' && step.message && (
                        <small>{step.message}</small>
                      )}
                    </li>
                  ))}
                </ol>
              )}
              {activeAnalysisJob.chunk_count && (
                <div className='chips'>
                  <span>Chunks planned: {activeAnalysisJob.chunk_count}</span>
                  <span>Mode: background</span>
                </div>
              )}
            </div>
          )}
          <p className='muted'>
            Kolejnosc: gora-lewo, gora-prawo, dol-prawo, dol-lewo. Boisko:
            30 x 47.4 m. Punkty: {pitchPoints.length}/4.
          </p>
          <AnalysisPreflightPanel
            match={selected}
            analysis={analysis}
            runtimeInfo={runtimeInfo}
            hasSavedPitchConfig={savedPitchConfig}
            hasPendingPitchPoints={pitchPoints.length === 4}
            canRun={canAnalyze}
            disabled={isBusy}
            isRunning={busyAction === 'analysis'}
            onApplyPreset={handleApplyAnalysisPreset}
            onRun={savePitchAndRunAnalysis}
          />
          <div className='pitch-canvas-wrap workflow-pitch'>
            <canvas
              ref={canvasRef}
              onClick={handleCanvasClick}
              className='pitch-canvas'
            />
          </div>
          <details className='debug-details'>
            <summary>Zaawansowane ustawienia analizy</summary>
            <AnalysisForm
              analysis={analysis}
              onChange={setAnalysis}
              runtimeInfo={runtimeInfo}
              onRun={runAnalysis}
              disabled={isBusy}
              isRunning={busyAction === 'analysis'}
              showRunButton={false}
            />
          </details>
        </section>
      )}

      {activeStep === 'analysis' && !selected && (
        <section className='card workflow-card'>
          <h2>Najpierw wybierz video</h2>
          <p className='muted'>Analiza jest dostepna po dodaniu albo wybraniu meczu.</p>
          <button type='button' onClick={() => setActiveStep('video')}>
            Wroc do wyboru video
          </button>
        </section>
      )}

      {activeStep === 'review' && selected && (
        <>
          <IdentityReviewWorkspace
            match={selected}
            initialWorkflow={reviewWorkflow}
            onWorkflowChanged={setReviewWorkflow}
            onOpenReport={() => setActiveStep('publish')}
          />
          <details className='debug-details'>
            <summary>Kadra meczu</summary>
            <MatchRosterPanel
              match={selected}
              disabled={isBusy}
              onSave={saveRosterTeams}
              onStatus={setStatus}
            />
          </details>
          <details
            className='debug-details'
            onToggle={(event) => setShowDeveloperDiagnostics(event.currentTarget.open)}
          >
            <summary>Developer / diagnostyka</summary>
            {showDeveloperDiagnostics && (
              <>
          <div className='artifact-box'>
            <h3>Techniczna paczka meczu</h3>
            <p className='muted'>Publikacja tworzy ją automatycznie. Ten przycisk służy wyłącznie do diagnostyki.</p>
            <button type='button' className='secondary' onClick={buildPackage} disabled={isBusy || !reviewWorkflow?.can_publish}>
              {busyAction === 'package' ? 'Generuje…' : 'Wygeneruj match_package.json'}
            </button>
          </div>
          <section className='card workflow-card'>
            <div className='row between'>
              <div>
                <h2>Stable overlay — diagnostyka</h2>
                <p className='muted'>
                  Techniczny podgląd stable ID do diagnostyki. Finalny operator QA odbywa się na reviewed video powyżej.
                </p>
              </div>
              {selected.analysis_report?.run_id && (
                <span className='confidence-pill medium'>
                  run {selected.analysis_report.run_id}
                </span>
              )}
            </div>
            {reviewStableOverlay ? (
              <video
                controls
                src={artifactUrl(selected.id, reviewStableOverlay)}
                className='video'
              />
            ) : reviewStableOverlaySkipped ? (
              <p className='muted'>
                Stable overlay video został pominięty w tym runie.
              </p>
            ) : (
              <p className='muted'>
                Brak stable_overlay_preview.mp4 dla tego meczu.
              </p>
            )}
          </section>
          <AnalysisQualityPanel match={selected} />
          <StablePlayersPanel
            match={selected}
            onStatus={setStatus}
            onSaved={() => refreshSelected('review')}
            includeOperatorTools={false}
          />
          <details className='debug-details'>
            <summary>Legacy: cropy, re-anchor i whole-subject review</summary>
            <SecondHalfIdentityReanchorPanel match={selected} onStatus={setStatus} />
            <IdentityRosterSubjectReviewPanel match={selected} onStatus={setStatus} />
            <IdentityCropReviewPanel
              match={selected}
              onStatus={setStatus}
              onSaved={() => refreshSelected('review')}
            />
          </details>
          <details className='debug-details'>
            <summary>Opcjonalnie: team config i team stats</summary>
            <TeamConfigPanel
              match={selected}
              onStatus={setStatus}
              onSaved={() => refreshSelected('review')}
            />
          </details>
          <details className='debug-details'>
            <summary>Opcjonalnie: artefakty i overlay debug</summary>
            <div className='artifact-box'>
              <div>
                <h3>Ball tracking quick test</h3>
                <p className='muted'>
                  Uruchamia tylko detekcje pilki. Nie przelicza stable ID ani
                  statystyk zawodnikow.
                </p>
              </div>
              <div className='row'>
                <button
                  type='button'
                  className='secondary'
                  disabled={isBusy}
                  onClick={() =>
                    setBallAnalysis({
                      max_seconds: 3,
                      frame_stride: 4,
                      yolo_model: DEFAULT_BALL_YOLO_MODEL,
                      yolo_conf: 0.05,
                      yolo_imgsz: 960,
                      yolo_device: analysis.yolo_device,
                    })
                  }
                >
                  Fast 3s
                </button>
                <button
                  type='button'
                  className='secondary'
                  disabled={isBusy}
                  onClick={() =>
                    setBallAnalysis({
                      max_seconds: 6,
                      frame_stride: 3,
                      yolo_model: DEFAULT_BALL_YOLO_MODEL,
                      yolo_conf: 0.04,
                      yolo_imgsz: 1280,
                      yolo_device: analysis.yolo_device,
                    })
                  }
                >
                  Balanced 6s
                </button>
                <button
                  type='button'
                  className='secondary'
                  disabled={isBusy}
                  onClick={() =>
                    setBallAnalysis({
                      max_seconds: 12,
                      frame_stride: 2,
                      yolo_model: DEFAULT_BALL_YOLO_MODEL,
                      yolo_conf: 0.03,
                      yolo_imgsz: 1280,
                      yolo_device: analysis.yolo_device,
                    })
                  }
                >
                  Full sample
                </button>
                <button
                  type='button'
                  className='secondary'
                  disabled={isBusy}
                  onClick={() =>
                    setBallAnalysis({
                      max_seconds: 12,
                      frame_stride: 1,
                      yolo_model: DEFAULT_BALL_YOLO_MODEL,
                      yolo_conf: 0.05,
                      yolo_imgsz: 1280,
                      yolo_device: analysis.yolo_device,
                    })
                  }
                >
                  Custom PT
                </button>
              </div>
              <div className='grid three compact'>
                <label>
                  Max seconds
                  <input
                    type='number'
                    min={1}
                    value={ballAnalysis.max_seconds}
                    disabled={isBusy}
                    onChange={(event) =>
                      setBallAnalysis({
                        ...ballAnalysis,
                        max_seconds: Number(event.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  Frame stride
                  <input
                    type='number'
                    min={1}
                    value={ballAnalysis.frame_stride}
                    disabled={isBusy}
                    onChange={(event) =>
                      setBallAnalysis({
                        ...ballAnalysis,
                        frame_stride: Number(event.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  Img size
                  <input
                    type='number'
                    min={320}
                    step={32}
                    value={ballAnalysis.yolo_imgsz}
                    disabled={isBusy}
                    onChange={(event) =>
                      setBallAnalysis({
                        ...ballAnalysis,
                        yolo_imgsz: Number(event.target.value),
                      })
                    }
                  />
                </label>
              </div>
              <div className='grid three compact'>
                <label>
                  Conf
                  <input
                    type='number'
                    min={0.01}
                    max={0.5}
                    step={0.01}
                    value={ballAnalysis.yolo_conf}
                    disabled={isBusy}
                    onChange={(event) =>
                      setBallAnalysis({
                        ...ballAnalysis,
                        yolo_conf: Number(event.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  Model
                  <input
                    value={ballAnalysis.yolo_model}
                    disabled={isBusy}
                    onChange={(event) =>
                      setBallAnalysis({
                        ...ballAnalysis,
                        yolo_model: event.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  Device
                  <input
                    value={ballAnalysis.yolo_device || ''}
                    disabled={isBusy}
                    onChange={(event) =>
                      setBallAnalysis({
                        ...ballAnalysis,
                        yolo_device: event.target.value || null,
                      })
                    }
                  />
                </label>
              </div>
              <div className='row between'>
                <p className='muted'>
                  Aktualnie: {ballAnalysis.max_seconds}s, stride{' '}
                  {ballAnalysis.frame_stride}, imgsz {ballAnalysis.yolo_imgsz},
                  conf {ballAnalysis.yolo_conf}
                </p>
                <button
                  type='button'
                  className='secondary'
                  onClick={runBallAnalysis}
                  disabled={isBusy}
                >
                  {busyAction === 'ball-analysis'
                    ? 'Testuje pilke...'
                    : 'Uruchom test pilki'}
                </button>
              </div>
            </div>
            <AnalysisArtifacts match={selected} />
          </details>
          <details
            className='debug-details'
            onToggle={(event) => setShowDeveloperDebug(event.currentTarget.open)}
          >
            <summary>Developer debug: legacy identity candidates i raw tracklety</summary>
            {showDeveloperDebug && (
              <>
                <p className='muted'>
                  Stare panele do recznego przypisywania raw trackletow.
                  Glowny workflow uzywa stable slotow i roster mapping.
                </p>
                <IdentityCandidatePanel
                  match={selected}
                  onStatus={setStatus}
                  onSaved={() => refreshSelected('review')}
                />
                <TrackletAssignmentPanel
                  match={selected}
                  onStatus={setStatus}
                  onSaved={() => refreshSelected('review')}
                />
              </>
            )}
          </details>
              </>
            )}
          </details>
        </>
      )}

      {activeStep === 'publish' && selected && (
        <>
          <section className='card workflow-card'>
            <div className='row between'>
              <div>
                <h2>4. Raport i publikacja</h2>
                <p className='muted'>
                  Otworz raport roboczy albo opublikuj snapshot do SQLite, zeby
                  byl widoczny na stronie glownej.
                </p>
              </div>
              <div className='row'>
                <Link to={`/matches/${encodeURIComponent(selected.id)}/report`}>
                  Raport roboczy
                </Link>
                {selected.published_match_id && (
                  <Link to={`/published/matches/${encodeURIComponent(selected.published_match_id)}/report`}>
                    Raport publiczny
                  </Link>
                )}
              </div>
            </div>
            <div className='chips'>
              <span>Status: {selected.status || 'uploaded'}</span>
              <span>Analysis: {selected.analysis_report?.status || 'missing'}</span>
              <span>Review: {reviewWorkflow?.review_complete ? 'zakończony ✓' : 'wymaga ukończenia'}</span>
              <span>Raport: {selected.published_match_id ? 'opublikowany' : 'gotowy do publikacji'}</span>
            </div>
            <div className='row'>
              <button
                type='button'
                onClick={() => publishSelected(Boolean(selected.published_match_id))}
                disabled={isBusy || !reviewWorkflow?.can_publish}
              >
                {busyAction === 'publish'
                  ? 'Publikuje...'
                  : selected.published_match_id
                    ? 'Zaktualizuj opublikowany raport'
                    : 'Opublikuj raport'}
              </button>
            </div>
          </section>
          <PublishedDatabasePanel onStatus={setStatus} />
        </>
      )}
    </main>
  );
}
