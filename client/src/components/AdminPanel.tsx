import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  analyzeMatch,
  createMatchPackage,
  frameUrl,
  getMatch,
  listMatches,
  publishLocalMatch,
  savePitch,
  updateMatchMetadata,
} from '../api';
import type { AnalysisPayload, Match, MatchMetadataPayload } from '../types';
import { errorMessage, drawPitchOverlay } from '../lib/helpers';
import { NewMatchForm } from './NewMatchForm';
import { MatchList } from './MatchList';
import { MatchSummary } from './MatchSummary';
import { MetadataEditor } from './MetadataEditor';
import { AnalysisForm } from './AnalysisForm';
import { AnalysisArtifacts } from './AnalysisArtifacts';
import { AnalysisQualityPanel } from './AnalysisQualityPanel';
import { TrackletAssignmentPanel } from './TrackletAssignmentPanel';
import { PublishedDatabasePanel } from './PublishedDatabasePanel';
import { IdentityCandidatePanel } from '../IdentityCandidatePanel';
import { StablePlayersPanel } from './StablePlayersPanel';
import { TeamConfigPanel } from './TeamConfigPanel';

const defaultAnalysis: AnalysisPayload = {
  adapter: 'yolo',
  max_seconds: 30,
  frame_stride: 1,
  yolo_model: 'yolov8n.pt',
  yolo_conf: 0.05,
  yolo_imgsz: 1920,
  yolo_tracker: 'centroid_high_recall',
  yolo_device: null,
};

type Point = [number, number];

export function AdminPanel() {
  const [matches, setMatches] = useState<Match[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState<Match | null>(null);
  const [status, setStatus] = useState('');
  const [analysis, setAnalysis] = useState<AnalysisPayload>(defaultAnalysis);
  const [frameSecond, setFrameSecond] = useState(1);
  const [frameSrc, setFrameSrc] = useState('');
  const [pitchPoints, setPitchPoints] = useState<Point[]>([]);
  const [showDeveloperDebug, setShowDeveloperDebug] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const isBusy = busyAction !== null;

  async function refresh(selectId?: string) {
    const items = await listMatches();
    setMatches(items);
    const nextId = selectId || selectedId || items[0]?.id || '';
    setSelectedId(nextId);
    if (nextId) setSelected(await getMatch(nextId));
  }

  useEffect(() => {
    refresh().catch((error) => setStatus(errorMessage(error)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    getMatch(selectedId)
      .then(setSelected)
      .catch((error) => setStatus(errorMessage(error)));
  }, [selectedId]);

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
    await refresh(match.id);
  }

  async function loadFrame() {
    if (!selectedId) return;
    setFrameSrc(frameUrl(selectedId, frameSecond));
    setPitchPoints([]);
  }

  function handleCanvasClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas || pitchPoints.length >= 4) return;
    const rect = canvas.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * canvas.width;
    const y = ((event.clientY - rect.top) / rect.height) * canvas.height;
    setPitchPoints((points) => [...points, [x, y]]);
  }

  async function persistPitch() {
    if (isBusy) return;
    if (!selectedId || pitchPoints.length !== 4) {
      setStatus('Kliknij dokładnie 4 rogi boiska.');
      return;
    }
    setBusyAction('pitch');
    setStatus('Zapisuję konfigurację boiska...');
    try {
      await savePitch(selectedId, {
        image_points: pitchPoints,
        width_m: 30,
        length_m: 47.4,
        pitch_dimensions_m: { width_m: 30, length_m: 47.4 },
        calibration_frame_time_sec: frameSecond,
        source: 'manual',
      });
      setStatus('Zapisano konfigurację boiska 30 x 47.4 m.');
      setSelected(await getMatch(selectedId));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function runAnalysis() {
    if (!selectedId || isBusy) return;
    setBusyAction('analysis');
    setStatus('Analiza uruchomiona...');
    try {
      await analyzeMatch(selectedId, analysis);
      setStatus(
        'Analiza zakończona. Sprawdź stabilnych zawodników i stable overlay.',
      );
      setSelected(await getMatch(selectedId));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function buildPackage() {
    if (!selectedId || isBusy) return;
    setBusyAction('package');
    setStatus('Generuję publishable match package...');
    try {
      await createMatchPackage(selectedId);
      setStatus('Wygenerowano match_package.json.');
      setSelected(await getMatch(selectedId));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function publishSelected(replace = false) {
    if (!selectedId || isBusy) return;
    setBusyAction('publish');
    setStatus(
      replace
        ? 'Nadpisuję mecz w bazie...'
        : 'Importuję mecz do bazy SQLite...',
    );
    try {
      const published = await publishLocalMatch(selectedId, replace);
      setStatus(`Mecz opublikowany w bazie jako ${published.id}.`);
      setSelected(await getMatch(selectedId));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function saveMetadata(payload: MatchMetadataPayload) {
    if (!selectedId || isBusy) return;
    setBusyAction('metadata');
    setStatus('Zapisuję metadane meczu...');
    try {
      const updated = await updateMatchMetadata(selectedId, payload);
      setSelected(updated);
      setStatus('Zapisano metadane meczu.');
      await refresh(updated.id);
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function refreshSelected() {
    if (!selectedId) return;
    setSelected(await getMatch(selectedId));
  }

  return (
    <main className='app'>
      <section className='hero'>
        <p className='eyebrow'>Local admin panel</p>
        <h1>Panel dodawania i analizy meczu</h1>
        <p>
          Ten widok jest do lokalnej pracy: upload video, wybor druzyn,
          kalibracja, analiza i publikacja do SQLite.
        </p>
        <div className='row'>
          <Link to='/'>Public viewer</Link>
          <Link to='/teams'>Rejestr drużyn</Link>
        </div>
      </section>

      {status && (
        <p className={isBusy ? 'status loading-status' : 'status'}>
          {isBusy && <span className='spinner' />}
          {status}
        </p>
      )}

      <div className='grid two'>
        <section className='card'>
          <h2>1. Dodaj mecz</h2>
          <NewMatchForm onCreated={handleCreated} onError={setStatus} />
        </section>
        <section className='card'>
          <h2>2. Wybierz mecz lokalny</h2>
          <MatchList
            matches={matches}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          {selected && <MatchSummary match={selected} />}
        </section>
      </div>

      {selected && (
        <div className='grid'>
          <section className='card'>
            <h2>3. Metadane meczu</h2>
            <MetadataEditor match={selected} onSave={saveMetadata} />
          </section>
          <section className='card'>
            <h2>4. Analiza meczu</h2>
            <div className='controls'>
              <label>
                Sekunda klatki do kalibracji
                <input
                  type='number'
                  value={frameSecond}
                  min={0}
                  step={0.5}
                  onChange={(event) =>
                    setFrameSecond(Number(event.target.value))
                  }
                />
              </label>
              <button type='button' onClick={loadFrame} disabled={isBusy}>
                Załaduj klatkę
              </button>
              <button
                type='button'
                onClick={() => setPitchPoints([])}
                disabled={isBusy}
              >
                Wyczyść punkty
              </button>
              <button type='button' onClick={persistPitch} disabled={isBusy}>
                {busyAction === 'pitch' ? 'Zapisuję boisko...' : 'Zapisz boisko'}
              </button>
            </div>
            <p className='muted'>
              Kliknij 4 rogi boiska: góra-lewo, góra-prawo, dół-prawo, dół-lewo.
              Boisko: 30 x 47.4 m. Punkty: {pitchPoints.length}/4.
            </p>
            <div className='pitch-canvas-wrap'>
              <canvas
                ref={canvasRef}
                onClick={handleCanvasClick}
                className='pitch-canvas'
              />
            </div>
            <AnalysisForm
              analysis={analysis}
              onChange={setAnalysis}
              onRun={runAnalysis}
              disabled={isBusy}
              isRunning={busyAction === 'analysis'}
            />
          </section>
        </div>
      )}

      {selected && (
        <StablePlayersPanel
          match={selected}
          onStatus={setStatus}
          onSaved={refreshSelected}
        />
      )}
      {selected && (
        <TeamConfigPanel
          match={selected}
          onStatus={setStatus}
          onSaved={refreshSelected}
        />
      )}
      {selected && <AnalysisQualityPanel match={selected} />}
      {selected && <AnalysisArtifacts match={selected} />}
      {selected && (
        <details
          className='debug-details'
          onToggle={(event) => setShowDeveloperDebug(event.currentTarget.open)}
        >
          <summary>Developer debug: legacy identity candidates i raw tracklety</summary>
          {showDeveloperDebug && (
            <>
              <p className='muted'>
                Stare panele do ręcznego przypisywania raw trackletów.
                Główny workflow używa teraz stable slotów i team config.
              </p>
              <IdentityCandidatePanel
                match={selected}
                onStatus={setStatus}
                onSaved={refreshSelected}
              />
              <TrackletAssignmentPanel
                match={selected}
                onStatus={setStatus}
                onSaved={refreshSelected}
              />
            </>
          )}
        </details>
      )}

      {selected && (
        <section className='card'>
          <h2>6. Publikacja do bazy</h2>
          <p className='muted'>
            Najpierw sprawdź stabilne ID i team assignment. Potem wygeneruj
            paczkę i zaimportuj snapshot do SQLite.
          </p>
          <div className='row'>
            <button type='button' onClick={buildPackage} disabled={isBusy}>
              {busyAction === 'package'
                ? 'Generuję paczkę...'
                : 'Generate match_package.json'}
            </button>
            <button
              type='button'
              onClick={() => publishSelected(false)}
              disabled={isBusy}
            >
              {busyAction === 'publish' ? 'Publikuję...' : 'Publish/import to DB'}
            </button>
            <button
              type='button'
              className='secondary'
              onClick={() => publishSelected(true)}
              disabled={isBusy}
            >
              Replace in DB
            </button>
          </div>
        </section>
      )}

      <PublishedDatabasePanel onStatus={setStatus} />
    </main>
  );
}
