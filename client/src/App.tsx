import { useEffect, useMemo, useRef, useState } from 'react';
import { analyzeMatch, artifactUrl, createMatch, frameUrl, getMatch, listMatches, savePitch } from './api';
import type { AnalysisPayload, AnalysisReport, Match } from './types';

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

type Point = [number, number];

const defaultAnalysis: AnalysisPayload = {
  adapter: 'yolo',
  max_seconds: 30,
  frame_stride: 1,
  yolo_model: 'yolov8n.pt',
  yolo_conf: 0.25,
  yolo_imgsz: 960,
  yolo_tracker: 'botsort.yaml',
  yolo_device: null
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function App() {
  const [matches, setMatches] = useState<Match[]>([]);
  const [selectedMatchId, setSelectedMatchId] = useState('');
  const [selectedMatch, setSelectedMatch] = useState<Match | null>(null);
  const [uploadTitle, setUploadTitle] = useState('Test match');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [frameSecond, setFrameSecond] = useState(1);
  const [frameSrc, setFrameSrc] = useState('');
  const [points, setPoints] = useState<Point[]>([]);
  const [pitchWidth, setPitchWidth] = useState(26);
  const [pitchLength, setPitchLength] = useState(56);
  const [analysis, setAnalysis] = useState<AnalysisPayload>(defaultAnalysis);
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [isBusy, setIsBusy] = useState(false);
  const [artifactCacheKey, setArtifactCacheKey] = useState(Date.now());
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);

  const selected = useMemo(() => matches.find((m) => m.id === selectedMatchId) || null, [matches, selectedMatchId]);

  function clearMessages() {
    setError('');
  }

  async function refresh() {
    clearMessages();
    const data = await listMatches();
    setMatches(data);
    if (!selectedMatchId && data.length) {
      setSelectedMatchId(data[0].id);
    }
  }

  useEffect(() => {
    refresh().catch((err) => setError(errorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedMatchId) return;
    clearMessages();
    getMatch(selectedMatchId)
      .then((m) => {
        setSelectedMatch(m);
        setReport(m.analysis_report || null);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [selectedMatchId]);

  function drawCanvas(img?: HTMLImageElement, pts = points) {
    const canvas = canvasRef.current;
    const image = img || imageRef.current;
    if (!canvas || !image) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

    if (pts.length > 0) {
      ctx.lineWidth = Math.max(2, canvas.width / 450);
      ctx.strokeStyle = '#facc15';
      ctx.fillStyle = '#ef4444';
      ctx.beginPath();
      pts.forEach((p, idx) => {
        if (idx === 0) ctx.moveTo(p[0], p[1]);
        else ctx.lineTo(p[0], p[1]);
      });
      if (pts.length === 4) ctx.closePath();
      ctx.stroke();

      pts.forEach((p, idx) => {
        ctx.fillStyle = '#ef4444';
        ctx.beginPath();
        ctx.arc(p[0], p[1], Math.max(6, canvas.width / 180), 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = 'white';
        ctx.font = `bold ${Math.max(16, canvas.width / 70)}px sans-serif`;
        ctx.fillText(String(idx + 1), p[0] + 10, p[1] - 10);
      });
    }
  }

  async function handleUpload() {
    if (!uploadFile) return alert('Wybierz plik video.');
    setIsBusy(true);
    clearMessages();
    setStatus('Uploading...');
    try {
      const match = await createMatch(uploadTitle, uploadFile);
      setStatus(`Uploaded match ${match.id}`);
      await refresh();
      setSelectedMatchId(match.id);
    } catch (err) {
      setError(errorMessage(err));
      setStatus('Upload failed.');
    } finally {
      setIsBusy(false);
    }
  }

  function loadFrame() {
    if (!selectedMatchId) return alert('Wybierz mecz.');
    clearMessages();
    const src = frameUrl(selectedMatchId, frameSecond);
    setFrameSrc(src);
    setPoints([]);
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => {
      imageRef.current = image;
      drawCanvas(image, []);
      setStatus(`Frame loaded: ${image.naturalWidth}×${image.naturalHeight}`);
    };
    image.onerror = () => {
      setError('Could not load calibration frame. Check backend logs and selected match video.');
    };
    image.src = src;
  }

  function handleCanvasClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas || !imageRef.current || points.length >= 4) return;

    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    // Map the click from the displayed CSS canvas size to the canvas internal pixel size.
    // The canvas CSS must preserve aspect ratio; see .pitch-canvas in styles.css.
    const x = ((event.clientX - rect.left) / rect.width) * canvas.width;
    const y = ((event.clientY - rect.top) / rect.height) * canvas.height;
    const clampedX = Math.min(canvas.width - 1, Math.max(0, x));
    const clampedY = Math.min(canvas.height - 1, Math.max(0, y));

    const next: Point[] = [...points, [Math.round(clampedX), Math.round(clampedY)]];
    setPoints(next);
    drawCanvas(undefined, next);
  }

  async function handleSavePitch() {
    if (!selectedMatchId) return alert('Wybierz mecz.');
    if (points.length !== 4) return alert('Kliknij dokładnie 4 punkty boiska.');
    setIsBusy(true);
    clearMessages();
    setStatus('Saving pitch config...');
    try {
      await savePitch(selectedMatchId, {
        image_points: points,
        width_m: pitchWidth,
        length_m: pitchLength,
        source: 'manual'
      });
      setStatus('Pitch config saved.');
      setSelectedMatch(await getMatch(selectedMatchId));
    } catch (err) {
      setError(errorMessage(err));
      setStatus('Saving pitch config failed.');
    } finally {
      setIsBusy(false);
    }
  }

  async function handleAnalyze() {
    if (!selectedMatchId) return alert('Wybierz mecz.');
    setIsBusy(true);
    clearMessages();
    setStatus('Analysis running... endpoint jest synchroniczny, więc UI wróci po zakończeniu.');
    setReport(null);
    try {
      const cleanPayload = { ...analysis, yolo_device: analysis.yolo_device?.trim() || null };
      const result = await analyzeMatch(selectedMatchId, cleanPayload);
      setReport(result);
      setArtifactCacheKey(Date.now());
      setStatus(result.status === 'completed' ? 'Analysis completed.' : 'Analysis finished with non-completed status.');
      setSelectedMatch(await getMatch(selectedMatchId));
    } catch (err) {
      setError(errorMessage(err));
      setStatus('Analysis failed. Check backend logs and analysis_report.json if it exists.');
      try {
        setSelectedMatch(await getMatch(selectedMatchId));
      } catch {
        // Ignore refresh failures after analysis errors.
      }
    } finally {
      setIsBusy(false);
    }
  }

  const overlaySrc = selectedMatchId && report?.artifacts ? `${artifactUrl(selectedMatchId, report.artifacts.overlay_preview)}?_=${artifactCacheKey}` : '';
  const heatmapSrc = selectedMatchId && report?.artifacts ? `${artifactUrl(selectedMatchId, report.artifacts.heatmap_all_tracks)}?_=${artifactCacheKey}` : '';

  return (
    <main className="app">
      <header className="hero">
        <div>
          <p className="eyebrow">Orlik Vision</p>
          <h1>Player ID flickering test + pitch calibration</h1>
          <p>React/Vite client + FastAPI backend + YOLO/Ultralytics adapter. Starter pod analizę nagrań z orlika.</p>
        </div>
      </header>

      {status && <div className="status">{status}</div>}
      {error && <div className="error-box"><strong>Error:</strong> {error}</div>}
      {report?.warnings && report.warnings.length > 0 && (
        <div className="warning-box">
          <strong>Warnings:</strong>
          <ul>
            {report.warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        </div>
      )}

      <section className="grid two">
        <div className="card">
          <h2>1. Upload video</h2>
          <label>Title</label>
          <input value={uploadTitle} onChange={(e) => setUploadTitle(e.target.value)} />
          <label>Video</label>
          <input type="file" accept="video/*" onChange={(e) => setUploadFile(e.target.files?.[0] || null)} />
          <button disabled={isBusy} onClick={handleUpload}>Upload</button>
        </div>

        <div className="card">
          <h2>2. Select match</h2>
          <div className="row">
            <select value={selectedMatchId} onChange={(e) => setSelectedMatchId(e.target.value)}>
              <option value="">-- select --</option>
              {matches.map((match) => (
                <option key={match.id} value={match.id}>
                  {match.id} — {match.title}
                </option>
              ))}
            </select>
            <button disabled={isBusy} onClick={() => refresh().catch((err) => setError(errorMessage(err)))}>Refresh</button>
          </div>
          {selected && (
            <p className="muted">
              {selected.video_filename} · {selected.video.width}×{selected.video.height} · {selected.video.fps} fps · {selected.video.duration_sec}s
            </p>
          )}
        </div>
      </section>

      <section className="card">
        <h2>3. Pitch calibration</h2>
        <p className="muted">Kliknij 4 rogi boiska: top-left, top-right, bottom-right, bottom-left. To tworzy pitch mask i homografię.</p>
        <div className="controls">
          <label>
            Frame second
            <input type="number" value={frameSecond} min={0} step={0.5} onChange={(e) => setFrameSecond(Number(e.target.value))} />
          </label>
          <label>
            Width m
            <input type="number" value={pitchWidth} onChange={(e) => setPitchWidth(Number(e.target.value))} />
          </label>
          <label>
            Length m
            <input type="number" value={pitchLength} onChange={(e) => setPitchLength(Number(e.target.value))} />
          </label>
        </div>
        <div className="row">
          <button disabled={isBusy} onClick={loadFrame}>Load frame</button>
          <button disabled={isBusy} onClick={() => { const next = points.slice(0, -1); setPoints(next); drawCanvas(undefined, next); }}>Undo point</button>
          <button disabled={isBusy} onClick={() => { setPoints([]); drawCanvas(undefined, []); }}>Clear</button>
          <button disabled={isBusy} onClick={handleSavePitch}>Save pitch config</button>
        </div>
        <p className="muted">Points: {points.length}/4</p>
        {frameSrc && (
          <div className="pitch-canvas-wrap">
            <canvas ref={canvasRef} onClick={handleCanvasClick} className="pitch-canvas" />
          </div>
        )}
        {canvasRef.current && (
          <p className="debug-small">
            Canvas internal size: {canvasRef.current.width}×{canvasRef.current.height}. If click drift appears, check that CSS does not stretch canvas aspect ratio.
          </p>
        )}
      </section>

      <section className="grid two">
        <div className="card">
          <h2>4. Run analysis</h2>
          <div className="controls vertical">
            <label>
              Adapter
              <select value={analysis.adapter} onChange={(e) => setAnalysis({ ...analysis, adapter: e.target.value as 'yolo' | 'motion' })}>
                <option value="yolo">YOLO / Ultralytics</option>
                <option value="motion">Motion fallback</option>
              </select>
            </label>
            <label>
              Max seconds
              <input type="number" value={analysis.max_seconds} onChange={(e) => setAnalysis({ ...analysis, max_seconds: Number(e.target.value) })} />
            </label>
            <label>
              Frame stride
              <input type="number" value={analysis.frame_stride} min={1} onChange={(e) => setAnalysis({ ...analysis, frame_stride: Number(e.target.value) })} />
            </label>
            <label>
              YOLO model
              <input value={analysis.yolo_model} onChange={(e) => setAnalysis({ ...analysis, yolo_model: e.target.value })} />
            </label>
            <label>
              YOLO tracker
              <select value={analysis.yolo_tracker} onChange={(e) => setAnalysis({ ...analysis, yolo_tracker: e.target.value })}>
                <option value="botsort.yaml">botsort.yaml</option>
                <option value="bytetrack.yaml">bytetrack.yaml</option>
              </select>
            </label>
            <label>
              Conf
              <input type="number" min={0.01} max={1} step={0.01} value={analysis.yolo_conf} onChange={(e) => setAnalysis({ ...analysis, yolo_conf: Number(e.target.value) })} />
            </label>
            <label>
              imgsz
              <input type="number" value={analysis.yolo_imgsz} onChange={(e) => setAnalysis({ ...analysis, yolo_imgsz: Number(e.target.value) })} />
            </label>
            <label>
              device
              <input placeholder="auto | cpu | 0" value={analysis.yolo_device || ''} onChange={(e) => setAnalysis({ ...analysis, yolo_device: e.target.value })} />
            </label>
          </div>
          <button disabled={isBusy} onClick={handleAnalyze}>Run analysis</button>
        </div>

        <div className="card">
          <h2>Match / report JSON</h2>
          <pre>{pretty(selectedMatch || report || {})}</pre>
        </div>
      </section>

      {selectedMatchId && report?.artifacts && (
        <section className="grid three">
          <div className="card artifact">
            <h2>Overlay preview</h2>
            <video
              controls
              src={overlaySrc}
              onError={() => setError('Browser could not play overlay_preview.mp4. Backend now transcodes to H.264; rebuild Docker and rerun analysis if this is an old artifact.')}
            />
            <a href={overlaySrc} target="_blank">Open video</a>
          </div>
          <div className="card artifact">
            <h2>Heatmap</h2>
            <img src={heatmapSrc} />
            <a href={heatmapSrc} target="_blank">Open heatmap</a>
          </div>
          <div className="card artifact">
            <h2>Data</h2>
            <a href={artifactUrl(selectedMatchId, report.artifacts.tracks_json)} target="_blank">tracks.json</a>
            <a href={artifactUrl(selectedMatchId, 'analysis_report.json')} target="_blank">analysis_report.json</a>
          </div>
        </section>
      )}
    </main>
  );
}
