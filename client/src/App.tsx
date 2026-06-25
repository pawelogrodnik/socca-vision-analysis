import { useEffect, useMemo, useRef, useState } from 'react';
import {
  analyzeMatch,
  artifactUrl,
  createMatch,
  createMatchPackage,
  deletePublishedMatch,
  frameUrl,
  getMatch,
  getPublishedMatch,
  getTrackletReview,
  listMatches,
  listPublishedMatches,
  publishLocalMatch,
  savePitch,
  savePlayerAssignments,
  updateMatchMetadata,
} from './api';
import type {
  AnalysisPayload,
  Match,
  MatchMetadataPayload,
  Player,
  PlayerAssignment,
  PublishedMatch,
  PublishedMatchDetail,
  Team,
  TrackletReviewState,
  TrackletSummary,
  TrackletAssignmentStatus,
} from './types';

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

type Point = [number, number];

const defaultAnalysis: AnalysisPayload = {
  adapter: 'yolo',
  max_seconds: 30,
  frame_stride: 1,
  yolo_model: 'yolov8n.pt',
  yolo_conf: 0.12,
  yolo_imgsz: 1280,
  yolo_tracker: 'botsort.yaml',
  yolo_device: null,
};

const emptyTeam = (name: string, color: string): Team => ({
  name,
  color,
  players: [],
});
const defaultTeams = (): Team[] => [
  emptyTeam('Team A', '#ef4444'),
  emptyTeam('Team B', '#2563eb'),
];

const assignmentStatuses: Array<{
  value: TrackletAssignmentStatus;
  label: string;
}> = [
  { value: 'unassigned', label: 'Do decyzji' },
  { value: 'assigned', label: 'Przypisany zawodnik' },
  { value: 'unknown', label: 'Nie wiem / później' },
  { value: 'false_positive', label: 'Fałszywa detekcja' },
  { value: 'opponent', label: 'Poza rosterem / inny mecz' },
  { value: 'referee', label: 'Sędzia / osoba techniczna' },
];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function parseRoster(value: string): Player[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [namePart, numberPart, rolePart] = line
        .split(',')
        .map((part) => part?.trim());
      const role = rolePart || 'player';
      return {
        name: namePart,
        number: numberPart || null,
        role,
        is_guest:
          role.toLowerCase().includes('guest') ||
          role.toLowerCase().includes('najem'),
      };
    });
}

function rosterToText(players: Player[]): string {
  return players
    .map((player) =>
      [player.name, player.number || '', player.role || 'player'].join(', '),
    )
    .join('\n');
}

function App() {
  const isAdmin = window.location.pathname.startsWith('/admin-panel');
  return isAdmin ? <AdminPanel /> : <Viewer />;
}

function Viewer() {
  const [matches, setMatches] = useState<PublishedMatch[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState<PublishedMatchDetail | null>(null);
  const [status, setStatus] = useState('');

  useEffect(() => {
    listPublishedMatches()
      .then((items) => {
        setMatches(items);
        if (items[0]) setSelectedId(items[0].id);
      })
      .catch((error) => setStatus(errorMessage(error)));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    getPublishedMatch(selectedId)
      .then(setSelected)
      .catch((error) => setStatus(errorMessage(error)));
  }, [selectedId]);

  return (
    <main className='app'>
      <section className='hero'>
        <p className='eyebrow'>Public viewer</p>
        <h1>Socca Vision Analysis</h1>
        <p>Read-only widok opublikowanych meczów z lekkiej bazy SQLite.</p>
        <a href='/admin-panel'>Przejdź do lokalnego admin panelu</a>
      </section>
      {status && <p className='status'>{status}</p>}
      <div className='grid two'>
        <section className='card'>
          <h2>Opublikowane mecze</h2>
          <PublishedMatchList
            matches={matches}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </section>
        <section className='card'>
          <h2>Publiczny raport</h2>
          {selected ? (
            <PublishedMatchSummary match={selected} />
          ) : (
            <p className='muted'>Brak opublikowanych meczów w bazie.</p>
          )}
        </section>
      </div>
    </main>
  );
}

function AdminPanel() {
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
      'Analiza zakończona. Przejdź do sekcji identity candidates i przypisz kandydatów do zawodników.',
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

  return (
    <main className='app'>
      <section className='hero'>
        <p className='eyebrow'>Local admin panel</p>
        <h1>Panel dodawania i analizy meczu</h1>
        <p>
          Ten widok jest do lokalnej pracy: upload video, drużyny, roster,
          kalibracja, YOLO i publikacja do SQLite.
        </p>
        <a href='/'>Public viewer</a>
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

      {selected && <AnalysisArtifacts match={selected} />}
      {selected && (
        <TrackletAssignmentPanel
          match={selected}
          onStatus={setStatus}
          onSaved={async () => setSelected(await getMatch(selected.id))}
        />
      )}

      {selected && (
        <section className='card'>
          <h2>6. Publikacja do bazy</h2>
          <p className='muted'>
            Najpierw zaakceptuj identity candidates i przypisz je do rosteru.
            Potem wygeneruj paczkę i zaimportuj snapshot do SQLite.
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

function drawPitchOverlay(ctx: CanvasRenderingContext2D, points: Point[]) {
  ctx.lineWidth = 4;
  ctx.strokeStyle = '#facc15';
  ctx.fillStyle = '#ef4444';
  if (points.length > 1) {
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    points.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
    if (points.length === 4) ctx.closePath();
    ctx.stroke();
  }
  points.forEach(([x, y], index) => {
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillText(String(index + 1), x + 10, y - 10);
  });
}

function NewMatchForm({
  onCreated,
  onError,
}: {
  onCreated: (match: Match) => void;
  onError: (message: string) => void;
}) {
  const [title, setTitle] = useState('Nowy mecz');
  const [matchDate, setMatchDate] = useState('');
  const [season, setSeason] = useState('2026');
  const [venue, setVenue] = useState('');
  const [format, setFormat] = useState('7v7');
  const [teams, setTeams] = useState<Team[]>(defaultTeams());
  const [video, setVideo] = useState<File | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!video) {
      onError('Wybierz plik video.');
      return;
    }
    try {
      const match = await createMatch({
        title,
        video,
        match_date: matchDate,
        season,
        venue,
        format,
        teams,
      });
      onCreated(match);
    } catch (error) {
      onError(errorMessage(error));
    }
  }

  return (
    <form onSubmit={submit} className='stack'>
      <label>
        Tytuł meczu
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <div className='grid three compact'>
        <label>
          Data
          <input
            type='date'
            value={matchDate}
            onChange={(event) => setMatchDate(event.target.value)}
          />
        </label>
        <label>
          Sezon
          <input
            value={season}
            onChange={(event) => setSeason(event.target.value)}
          />
        </label>
        <label>
          Format
          <input
            value={format}
            onChange={(event) => setFormat(event.target.value)}
          />
        </label>
      </div>
      <label>
        Miejsce
        <input
          value={venue}
          onChange={(event) => setVenue(event.target.value)}
        />
      </label>
      <TeamEditor teams={teams} onChange={setTeams} />
      <label>
        Video
        <input
          type='file'
          accept='video/*'
          onChange={(event) => setVideo(event.target.files?.[0] || null)}
        />
      </label>
      <button type='submit'>Dodaj mecz</button>
    </form>
  );
}

function MetadataEditor({
  match,
  onSave,
}: {
  match: Match;
  onSave: (payload: MatchMetadataPayload) => void;
}) {
  const [title, setTitle] = useState(match.title);
  const [matchDate, setMatchDate] = useState(match.match_date || '');
  const [season, setSeason] = useState(match.season || '');
  const [venue, setVenue] = useState(match.venue || '');
  const [format, setFormat] = useState(match.format || '7v7');
  const [status, setStatus] = useState(match.status || 'uploaded');
  const [teams, setTeams] = useState<Team[]>(match.teams || defaultTeams());

  useEffect(() => {
    setTitle(match.title);
    setMatchDate(match.match_date || '');
    setSeason(match.season || '');
    setVenue(match.venue || '');
    setFormat(match.format || '7v7');
    setStatus(match.status || 'uploaded');
    setTeams(match.teams || defaultTeams());
  }, [match]);

  return (
    <div className='stack'>
      <label>
        Tytuł
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <div className='grid three compact'>
        <label>
          Data
          <input
            type='date'
            value={matchDate}
            onChange={(event) => setMatchDate(event.target.value)}
          />
        </label>
        <label>
          Sezon
          <input
            value={season}
            onChange={(event) => setSeason(event.target.value)}
          />
        </label>
        <label>
          Status
          <input
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          />
        </label>
      </div>
      <label>
        Miejsce
        <input
          value={venue}
          onChange={(event) => setVenue(event.target.value)}
        />
      </label>
      <label>
        Format
        <input
          value={format}
          onChange={(event) => setFormat(event.target.value)}
        />
      </label>
      <TeamEditor teams={teams} onChange={setTeams} />
      <button
        type='button'
        onClick={() =>
          onSave({
            title,
            match_date: matchDate || null,
            season: season || null,
            venue: venue || null,
            format,
            status,
            teams,
          })
        }
      >
        Zapisz metadane
      </button>
    </div>
  );
}

function TeamEditor({
  teams,
  onChange,
}: {
  teams: Team[];
  onChange: (teams: Team[]) => void;
}) {
  function updateTeam(index: number, patch: Partial<Team>) {
    onChange(
      teams.map((team, teamIndex) =>
        teamIndex === index ? { ...team, ...patch } : team,
      ),
    );
  }

  return (
    <div className='stack'>
      <div className='row between'>
        <strong>Drużyny i roster</strong>
        <button
          type='button'
          onClick={() =>
            onChange([
              ...teams,
              emptyTeam(`Team ${teams.length + 1}`, '#64748b'),
            ])
          }
        >
          Dodaj drużynę
        </button>
      </div>
      {teams.map((team, index) => (
        <div className='team-card' key={team.id || index}>
          <div className='grid three compact'>
            <label>
              Nazwa
              <input
                value={team.name}
                onChange={(event) =>
                  updateTeam(index, { name: event.target.value })
                }
              />
            </label>
            <label>
              Kolor
              <input
                type='color'
                value={team.color || '#64748b'}
                onChange={(event) =>
                  updateTeam(index, { color: event.target.value })
                }
              />
            </label>
            <button
              type='button'
              onClick={() =>
                onChange(teams.filter((_, teamIndex) => teamIndex !== index))
              }
            >
              Usuń
            </button>
          </div>
          <label>
            Zawodnicy: imię, numer, rola — jeden na linię
            <textarea
              value={rosterToText(team.players || [])}
              onChange={(event) =>
                updateTeam(index, { players: parseRoster(event.target.value) })
              }
              rows={5}
            />
          </label>
        </div>
      ))}
    </div>
  );
}

function AnalysisForm({
  analysis,
  onChange,
  onRun,
}: {
  analysis: AnalysisPayload;
  onChange: (payload: AnalysisPayload) => void;
  onRun: () => void;
}) {
  return (
    <div className='analysis-form'>
      <h3>Ustawienia YOLO</h3>
      <div className='grid three compact'>
        <label>
          Adapter
          <select
            value={analysis.adapter}
            onChange={(event) =>
              onChange({
                ...analysis,
                adapter: event.target.value as AnalysisPayload['adapter'],
              })
            }
          >
            <option value='yolo'>yolo</option>
            <option value='motion'>motion</option>
          </select>
        </label>
        <label>
          Max seconds
          <input
            type='number'
            value={analysis.max_seconds}
            onChange={(event) =>
              onChange({ ...analysis, max_seconds: Number(event.target.value) })
            }
          />
        </label>
        <label>
          Frame stride
          <input
            type='number'
            value={analysis.frame_stride}
            min={1}
            onChange={(event) =>
              onChange({
                ...analysis,
                frame_stride: Number(event.target.value),
              })
            }
          />
        </label>
      </div>
      <div className='grid three compact'>
        <label>
          Model
          <input
            value={analysis.yolo_model}
            onChange={(event) =>
              onChange({ ...analysis, yolo_model: event.target.value })
            }
          />
        </label>
        <label>
          Conf
          <input
            type='number'
            step='0.01'
            value={analysis.yolo_conf}
            onChange={(event) =>
              onChange({ ...analysis, yolo_conf: Number(event.target.value) })
            }
          />
        </label>
        <label>
          Img size
          <input
            type='number'
            value={analysis.yolo_imgsz}
            onChange={(event) =>
              onChange({ ...analysis, yolo_imgsz: Number(event.target.value) })
            }
          />
        </label>
      </div>
      <div className='grid two compact'>
        <label>
          Tracker
          <input
            value={analysis.yolo_tracker}
            onChange={(event) =>
              onChange({ ...analysis, yolo_tracker: event.target.value })
            }
          />
        </label>
        <label>
          Device
          <input
            value={analysis.yolo_device || ''}
            onChange={(event) =>
              onChange({ ...analysis, yolo_device: event.target.value || null })
            }
          />
        </label>
      </div>
      <button type='button' onClick={onRun}>
        Uruchom analizę
      </button>
    </div>
  );
}

function MatchList({
  matches,
  selectedId,
  onSelect,
}: {
  matches: Match[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  if (!matches.length) return <p className='muted'>Brak meczów.</p>;
  return (
    <div className='match-list'>
      {matches.map((match) => (
        <button
          type='button'
          className={
            match.id === selectedId ? 'match-item active' : 'match-item'
          }
          key={match.id}
          onClick={() => onSelect(match.id)}
        >
          <strong>{match.title}</strong>
          <span>
            {match.match_date || 'brak daty'} · {match.status || 'uploaded'}
          </span>
        </button>
      ))}
    </div>
  );
}

function PublishedMatchList({
  matches,
  selectedId,
  onSelect,
}: {
  matches: PublishedMatch[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  if (!matches.length)
    return <p className='muted'>Brak opublikowanych meczów.</p>;
  return (
    <div className='match-list'>
      {matches.map((match) => (
        <button
          type='button'
          className={
            match.id === selectedId ? 'match-item active' : 'match-item'
          }
          key={match.id}
          onClick={() => onSelect(match.id)}
        >
          <strong>{match.title}</strong>
          <span>
            {match.match_date || 'brak daty'} · {match.player_count} zawodników
            · {match.tracks_count ?? 0} tracków
          </span>
        </button>
      ))}
    </div>
  );
}

function MatchSummary({ match }: { match: Match }) {
  const playerCount = useMemo(
    () =>
      (match.teams || []).reduce(
        (sum, team) => sum + (team.players?.length || 0),
        0,
      ),
    [match.teams],
  );
  return (
    <div className='summary'>
      <h3>{match.title}</h3>
      <p className='muted'>
        {match.match_date || 'brak daty'} · {match.season || 'brak sezonu'} ·{' '}
        {match.venue || 'brak miejsca'}
      </p>
      <div className='chips'>
        <span>Status: {match.status || 'uploaded'}</span>
        <span>Format: {match.format || '7v7'}</span>
        <span>Drużyny: {(match.teams || []).length}</span>
        <span>Zawodnicy: {playerCount}</span>
        {match.published_match_id && (
          <span>DB: {match.published_match_id}</span>
        )}
      </div>
      {(match.teams || []).map((team) => (
        <div className='team-row' key={team.id || team.name}>
          <span
            className='color-dot'
            style={{ background: team.color || '#64748b' }}
          />
          <strong>{team.name}</strong>
          <span className='muted'>{team.players?.length || 0} zawodników</span>
        </div>
      ))}
    </div>
  );
}

function PublishedMatchSummary({ match }: { match: PublishedMatchDetail }) {
  const source = match.package?.match;
  return (
    <div className='summary'>
      <h3>{match.title}</h3>
      <p className='muted'>
        {match.match_date || 'brak daty'} · {match.season || 'brak sezonu'} ·{' '}
        {match.venue || 'brak miejsca'}
      </p>
      <div className='chips'>
        <span>Status: {match.status}</span>
        <span>Drużyny: {match.team_count}</span>
        <span>Zawodnicy: {match.player_count}</span>
        <span>Tracki: {match.tracks_count ?? 0}</span>
        <span>Klatki: {match.frames_processed ?? 0}</span>
        <span>Warnings: {match.warnings_count}</span>
      </div>
      {(source?.teams || []).map((team) => (
        <div className='team-row' key={team.id || team.name}>
          <span
            className='color-dot'
            style={{ background: team.color || '#64748b' }}
          />
          <strong>{team.name}</strong>
          <span className='muted'>{team.players?.length || 0} zawodników</span>
        </div>
      ))}
      {match.package?.player_assignments?.summary && (
        <>
          <h4>Zaakceptowane przypisania</h4>
          <AssignmentSummaryChips
            matchTeams={source?.teams || []}
            summary={match.package.player_assignments.summary}
          />
        </>
      )}
      <h4>Analysis snapshot</h4>
      <pre>
        {pretty(
          match.package?.analysis_report || { status: 'no analysis report' },
        )}
      </pre>
    </div>
  );
}

function AnalysisArtifacts({ match }: { match: Match }) {
  const report = match.analysis_report;
  const heatmap = report?.artifacts?.heatmap_all_tracks;
  const overlay = report?.artifacts?.overlay_preview;
  return (
    <section className='card'>
      <h2>Widok analizy</h2>
      <div className='grid two'>
        <div>
          <h3>Artefakty lokalne</h3>
          {overlay && (
            <video
              controls
              src={artifactUrl(match.id, overlay)}
              className='video'
            />
          )}
          {heatmap && (
            <img
              src={artifactUrl(match.id, heatmap)}
              className='heatmap'
              alt='Heatmap'
            />
          )}
          {match.match_package && (
            <a href={artifactUrl(match.id, 'match_package.json')}>
              Pobierz match_package.json
            </a>
          )}
          {match.player_assignments && (
            <a href={artifactUrl(match.id, 'player_assignments.json')}>
              Pobierz player_assignments.json
            </a>
          )}
        </div>
        <div>
          <h3>Analysis report</h3>
          <pre>{pretty(report || { status: 'not analyzed' })}</pre>
        </div>
      </div>
    </section>
  );
}

function AssignmentSummaryChips({
  matchTeams,
  summary,
}: {
  matchTeams: Team[];
  summary: TrackletReviewState['summary'];
}) {
  return (
    <div className='chips'>
      <span>Raw tracklety: {summary.raw_tracklets}</span>
      <span>Przypisane tracklety: {summary.assigned_tracklets}</span>
      <span>Nieprzypisane: {summary.unassigned_tracklets}</span>
      <span>Ignored: {summary.ignored_tracklets}</span>
      <span>Unikalni zawodnicy: {summary.unique_players_total}</span>
      {matchTeams?.map((team) => {
        const teamId = team.id || team.name;
        return (
          <span key={teamId}>
            {team.name}: {summary.unique_players_by_team[teamId] || 0}/
            {summary.roster_players_by_team[teamId] ||
              team.players?.length ||
              0}{' '}
            graczy
          </span>
        );
      })}
    </div>
  );
}

function TrackletAssignmentPanel({
  match,
  onStatus,
  onSaved,
}: {
  match: Match;
  onStatus: (message: string) => void;
  onSaved: () => void;
}) {
  const [review, setReview] = useState<TrackletReviewState | null>(null);
  const [assignments, setAssignments] = useState<PlayerAssignment[]>([]);
  const [selectedTrackletId, setSelectedTrackletId] = useState<number | null>(
    null,
  );

  async function load() {
    try {
      const data = await getTrackletReview(match.id);
      setReview(data);
      setAssignments(data.assignments);
      setSelectedTrackletId(data.tracklets[0]?.tracklet_id ?? null);
      onStatus('Załadowano tracklety do akceptacji.');
    } catch (error) {
      onStatus(`Nie mogę załadować trackletów: ${errorMessage(error)}`);
    }
  }

  useEffect(() => {
    if (match.analysis_report?.status === 'completed') {
      load().catch((error) => onStatus(errorMessage(error)));
    } else {
      setReview(null);
      setAssignments([]);
      setSelectedTrackletId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.analysis_report?.status]);

  function assignmentFor(trackletId: number): PlayerAssignment {
    return (
      assignments.find(
        (assignment) => assignment.tracklet_id === trackletId,
      ) || {
        tracklet_id: trackletId,
        status: 'unassigned',
        team_id: null,
        player_id: null,
        notes: '',
      }
    );
  }

  function updateAssignment(
    trackletId: number,
    patch: Partial<PlayerAssignment>,
  ) {
    const current = assignmentFor(trackletId);
    const next = { ...current, ...patch };
    if (next.status !== 'assigned') {
      next.team_id = patch.team_id ?? next.team_id;
      next.player_id = null;
    }
    setAssignments((items) => {
      const exists = items.some((item) => item.tracklet_id === trackletId);
      return exists
        ? items.map((item) => (item.tracklet_id === trackletId ? next : item))
        : [...items, next];
    });
  }

  async function save() {
    const saved = await savePlayerAssignments(match.id, assignments);
    onStatus(
      `Zapisano przypisania: ${saved.summary.assigned_tracklets} trackletów przypisanych, ${saved.summary.unique_players_total} unikalnych zawodników.`,
    );
    const fresh = await getTrackletReview(match.id);
    setReview(fresh);
    setAssignments(fresh.assignments);
    await onSaved();
  }

  if (match.analysis_report?.status !== 'completed') {
    return (
      <section className='card'>
        <h2>5. Akceptacja trackletów i player_id</h2>
        <p className='muted'>
          Uruchom analizę, żeby dostać listę surowych trackletów do przypisania
          zawodnikom.
        </p>
      </section>
    );
  }

  if (!review) {
    return (
      <section className='card'>
        <h2>5. Akceptacja trackletów i player_id</h2>
        <button type='button' onClick={load}>
          Załaduj tracklety
        </button>
      </section>
    );
  }

  const selectedTracklet =
    review.tracklets.find(
      (tracklet) => tracklet.tracklet_id === selectedTrackletId,
    ) || review.tracklets[0];
  const selectedAssignment = selectedTracklet
    ? assignmentFor(selectedTracklet.tracklet_id)
    : null;
  const selectedTeam = match?.teams?.find(
    (team) => team.id === selectedAssignment?.team_id,
  );

  return (
    <section className='card'>
      <div className='row between'>
        <div>
          <h2>5. Akceptacja trackletów i player_id</h2>
          <p className='muted'>
            YOLO/BoT-SORT daje surowe tracklety. Tutaj akceptujesz, czy to
            prawdziwy zawodnik i łączysz tracklet z graczem z rosteru.
          </p>
        </div>
        <div className='row'>
          <button type='button' onClick={load}>
            Odśwież tracklety
          </button>
          <button type='button' onClick={save}>
            Zapisz przypisania
          </button>
        </div>
      </div>

      <AssignmentSummaryChips
        matchTeams={match.teams || []}
        summary={review.summary}
      />

      <div className='grid two resolver-grid'>
        <div className='tracklet-list'>
          {review.tracklets.map((tracklet) => {
            const assignment = assignmentFor(tracklet.tracklet_id);
            return (
              <button
                type='button'
                className={
                  tracklet.tracklet_id === selectedTracklet?.tracklet_id
                    ? 'match-item active'
                    : 'match-item'
                }
                key={tracklet.tracklet_id}
                onClick={() => setSelectedTrackletId(tracklet.tracklet_id)}
              >
                <strong>
                  T{tracklet.tracklet_id} · {assignment.status}
                </strong>
                <span>
                  {Number(tracklet.duration_sec || 0).toFixed(1)}s ·{' '}
                  {tracklet.positions_count || 0} punktów · conf{' '}
                  {tracklet.avg_confidence ?? 'n/a'}
                </span>
              </button>
            );
          })}
        </div>

        <div className='team-card'>
          {selectedTracklet && selectedAssignment ? (
            <div className='stack'>
              <h3>Tracklet T{selectedTracklet.tracklet_id}</h3>
              <div className='chips'>
                <span>
                  Czas: {selectedTracklet.start_time_sec ?? '?'}s →{' '}
                  {selectedTracklet.end_time_sec ?? '?'}s
                </span>
                <span>
                  Długość:{' '}
                  {Number(selectedTracklet.duration_sec || 0).toFixed(1)}s
                </span>
                <span>Pozycje: {selectedTracklet.positions_count || 0}</span>
                <span>Conf: {selectedTracklet.avg_confidence ?? 'n/a'}</span>
              </div>
              <label>
                Status
                <select
                  value={selectedAssignment.status}
                  onChange={(event) =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      status: event.target.value as TrackletAssignmentStatus,
                    })
                  }
                >
                  {assignmentStatuses.map((status) => (
                    <option key={status.value} value={status.value}>
                      {status.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Drużyna
                <select
                  value={selectedAssignment.team_id || ''}
                  onChange={(event) =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      team_id: event.target.value || null,
                      player_id: null,
                      status: 'assigned',
                    })
                  }
                >
                  <option value=''>-- wybierz drużynę --</option>
                  {(match.teams || []).map((team) => (
                    <option
                      key={team.id || team.name}
                      value={team.id || team.name}
                    >
                      {team.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Zawodnik
                <select
                  value={selectedAssignment.player_id || ''}
                  onChange={(event) =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      player_id: event.target.value || null,
                      status: event.target.value
                        ? 'assigned'
                        : selectedAssignment.status,
                    })
                  }
                  disabled={!selectedTeam}
                >
                  <option value=''>-- wybierz zawodnika --</option>
                  {(selectedTeam?.players || []).map((player) => (
                    <option
                      key={player.id || player.name}
                      value={player.id || player.name}
                    >
                      {player.number ? `#${player.number} ` : ''}
                      {player.name} · {player.role}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Notatka
                <textarea
                  rows={3}
                  value={selectedAssignment.notes || ''}
                  onChange={(event) =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      notes: event.target.value,
                    })
                  }
                />
              </label>
              <div className='row'>
                <button
                  type='button'
                  onClick={() =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      status: 'false_positive',
                      team_id: null,
                      player_id: null,
                    })
                  }
                >
                  Oznacz false positive
                </button>
                <button
                  type='button'
                  className='secondary'
                  onClick={() =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      status: 'unknown',
                      team_id: null,
                      player_id: null,
                    })
                  }
                >
                  Zostaw unknown
                </button>
              </div>
              <pre>{pretty(selectedTracklet)}</pre>
            </div>
          ) : (
            <p className='muted'>Brak trackletów.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function PublishedDatabasePanel({
  onStatus,
}: {
  onStatus: (message: string) => void;
}) {
  const [matches, setMatches] = useState<PublishedMatch[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState<PublishedMatchDetail | null>(null);

  async function refresh(nextSelectedId = selectedId) {
    const items = await listPublishedMatches();
    setMatches(items);
    const id = nextSelectedId || items[0]?.id || '';
    setSelectedId(id);
    setSelected(id ? await getPublishedMatch(id) : null);
  }

  useEffect(() => {
    refresh().catch((error) => onStatus(errorMessage(error)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function select(id: string) {
    setSelectedId(id);
    setSelected(await getPublishedMatch(id));
  }

  async function remove(id: string) {
    const confirmed = window.confirm(
      'Usunąć opublikowany mecz z bazy? Tego używamy przy duplikatach albo błędnych statystykach.',
    );
    if (!confirmed) return;
    await deletePublishedMatch(id);
    onStatus(`Usunięto ${id} z bazy.`);
    await refresh('');
  }

  return (
    <section className='card'>
      <div className='row between'>
        <h2>7. Zarządzanie opublikowanymi statystykami</h2>
        <button type='button' onClick={() => refresh()}>
          Odśwież bazę
        </button>
      </div>
      <p className='muted'>
        To są mecze zaimportowane do SQLite. Tu można usunąć duplikaty albo
        błędne snapshoty statystyk.
      </p>
      <div className='grid two'>
        <PublishedMatchList
          matches={matches}
          selectedId={selectedId}
          onSelect={select}
        />
        <div>
          {selected ? (
            <>
              <PublishedMatchSummary match={selected} />
              <div className='row'>
                <button
                  type='button'
                  className='danger'
                  onClick={() => remove(selected.id)}
                >
                  Usuń z bazy
                </button>
              </div>
            </>
          ) : (
            <p className='muted'>Brak rekordów w bazie.</p>
          )}
        </div>
      </div>
    </section>
  );
}

export default App;
