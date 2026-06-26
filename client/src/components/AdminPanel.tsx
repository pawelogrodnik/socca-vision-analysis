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
import { TrackletAssignmentPanel } from './TrackletAssignmentPanel';
import { PublishedDatabasePanel } from './PublishedDatabasePanel';
import { IdentityCandidatePanel } from '../IdentityCandidatePanel';
import { StablePlayersPanel } from './StablePlayersPanel';

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
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

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
    if (!selectedId || pitchPoints.length !== 4) {
      setStatus('Kliknij dokładnie 4 rogi boiska.');
      return;
    }
    await savePitch(selectedId, {
      image_points: pitchPoints,
      width_m: 30,
      length_m: 47.4,
      source: 'manual',
    });
    setStatus('Zapisano konfigurację boiska 30 x 47.4 m.');
    setSelected(await getMatch(selectedId));
  }

  async function runAnalysis() {
    if (!selectedId) return;
    setStatus('Analiza uruchomiona...');
    await analyzeMatch(selectedId, analysis);
    setStatus(
      'Analiza zakończona. Sprawdź stabilnych zawodników i stable overlay.',
    );
    setSelected(await getMatch(selectedId));
  }

  async function buildPackage() {
    if (!selectedId) return;
    setStatus('Generuję publishable match package...');
    await createMatchPackage(selectedId);
    setStatus('Wygenerowano match_package.json.');
    setSelected(await getMatch(selectedId));
  }

  async function publishSelected(replace = false) {
    if (!selectedId) return;
    setStatus(
      replace
        ? 'Nadpisuję mecz w bazie...'
        : 'Importuję mecz do bazy SQLite...',
    );
    const published = await publishLocalMatch(selectedId, replace);
    setStatus(`Mecz opublikowany w bazie jako ${published.id}.`);
    setSelected(await getMatch(selectedId));
  }

  async function saveMetadata(payload: MatchMetadataPayload) {
    if (!selectedId) return;
    const updated = await updateMatchMetadata(selectedId, payload);
    setSelected(updated);
    setStatus('Zapisano metadane meczu, drużyny i zawodników.');
    await refresh(updated.id);
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
          Ten widok jest do lokalnej pracy: upload video, drużyny, roster,
          kalibracja, YOLO i publikacja do SQLite.
        </p>
        <Link to='/'>Public viewer</Link>
      </section>

      {status && <p className='status'>{status}</p>}

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
        <div className='grid two'>
          <section className='card'>
            <h2>3. Drużyny i zawodnicy</h2>
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
              <button type='button' onClick={loadFrame}>
                Załaduj klatkę
              </button>
              <button type='button' onClick={() => setPitchPoints([])}>
                Wyczyść punkty
              </button>
              <button type='button' onClick={persistPitch}>
                Zapisz boisko
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
      {selected && <AnalysisArtifacts match={selected} />}
      {selected && (
        <details className='debug-details'>
          <summary>Debug: raw identity candidates i tracklety</summary>
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
            <button type='button' onClick={buildPackage}>
              Generate match_package.json
            </button>
            <button type='button' onClick={() => publishSelected(false)}>
              Publish/import to DB
            </button>
            <button
              type='button'
              className='secondary'
              onClick={() => publishSelected(true)}
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
