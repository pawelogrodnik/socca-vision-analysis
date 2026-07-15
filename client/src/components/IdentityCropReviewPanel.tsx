import { useEffect, useMemo, useState, type MouseEvent } from 'react';
import {
  artifactUrl,
  generateIdentityReviewGallery,
  getIdentityCropReview,
  saveIdentityCropReview,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  IdentityCropAssignmentStatus,
  IdentityCropReviewCrop,
  IdentityCropReviewDocument,
  IdentityCropReviewUpdate,
  Match,
  Team,
} from '../types';
import { updateCropSelection } from '../utils/cropSelection';
import { cropSimilarityDistance, sortCropsBySimilarity } from '../utils/cropSimilarity';

interface IdentityCropReviewPanelProps {
  match: Match;
  onStatus: (message: string) => void;
  onSaved: () => Promise<void> | void;
}

type TeamFilter = 'A' | 'B' | 'U' | 'all';
type QueueFilter = 'remaining' | 'assigned' | 'flagged' | 'all';

type RosterOption = {
  id: string;
  name: string;
  number?: string | null;
  teamLabel: 'A' | 'B' | 'U';
};

const PAGE_SIZE = 160;

function teamLabel(index: number): 'A' | 'B' | 'U' {
  if (index === 0) return 'A';
  if (index === 1) return 'B';
  return 'U';
}

function rosterOptions(teams: Team[]): RosterOption[] {
  return teams.flatMap((team, index) =>
    (team.players || []).flatMap((player) =>
      player.id
        ? [{ id: String(player.id), name: player.name, number: player.number, teamLabel: teamLabel(index) }]
        : [],
    ),
  );
}

function matchesQueue(crop: IdentityCropReviewCrop, filter: QueueFilter): boolean {
  if (filter === 'remaining') return crop.status === 'unassigned';
  if (filter === 'assigned') return crop.status === 'assigned';
  if (filter === 'flagged') return ['unknown', 'wrong_team', 'false_positive'].includes(crop.status);
  return true;
}

function cropTime(crop: IdentityCropReviewCrop): string {
  return typeof crop.time_sec === 'number' ? `${crop.time_sec.toFixed(1)}s` : `f${crop.frame}`;
}

export function IdentityCropReviewPanel({ match, onStatus, onSaved }: IdentityCropReviewPanelProps) {
  const [review, setReview] = useState<IdentityCropReviewDocument | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [teamFilter, setTeamFilter] = useState<TeamFilter>('A');
  const [queueFilter, setQueueFilter] = useState<QueueFilter>('remaining');
  const [selectedPlayerId, setSelectedPlayerId] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchorArtifact, setAnchorArtifact] = useState<string | null>(null);
  const [similaritySeedArtifact, setSimilaritySeedArtifact] = useState<string | null>(null);
  const [visibleLimit, setVisibleLimit] = useState(PAGE_SIZE);

  const roster = useMemo(() => rosterOptions(review?.roster || match.teams || []), [match.teams, review?.roster]);
  const filteredRoster = useMemo(
    () => teamFilter === 'all' ? roster : roster.filter((player) => player.teamLabel === teamFilter),
    [roster, teamFilter],
  );
  const filteredCrops = useMemo(
    () => (review?.crops || []).filter(
      (crop) => (teamFilter === 'all' || crop.team_label === teamFilter) && matchesQueue(crop, queueFilter),
    ),
    [queueFilter, review?.crops, teamFilter],
  );
  const orderedCrops = useMemo(
    () => sortCropsBySimilarity(filteredCrops, similaritySeedArtifact),
    [filteredCrops, similaritySeedArtifact],
  );
  const similaritySeed = useMemo(
    () => filteredCrops.find((crop) => crop.artifact === similaritySeedArtifact) || null,
    [filteredCrops, similaritySeedArtifact],
  );
  const renderedCrops = orderedCrops.slice(0, visibleLimit);
  const selectedVisible = filteredCrops.filter((crop) => selected.has(crop.artifact)).length;

  async function load() {
    setLoading(true);
    try {
      const data = await getIdentityCropReview(match.id);
      setReview(data);
      onStatus(`Galeria cropow: ${data.summary.remaining} pozostalo do oznaczenia.`);
    } catch (error) {
      setReview(null);
      onStatus(`Nie mozna zaladowac galerii cropow: ${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  }

  async function generate() {
    setGenerating(true);
    try {
      onStatus('Generuje adaptacyjna galerie cropow identity review...');
      await generateIdentityReviewGallery(match.id, undefined, true);
      const data = await getIdentityCropReview(match.id);
      setReview(data);
      setOpen(true);
      setSimilaritySeedArtifact(null);
      await onSaved();
      onStatus(`Gotowe: wygenerowano ${data.summary.crops_total} cropow.`);
    } catch (error) {
      setReview(null);
      onStatus(`Nie udalo sie wygenerowac cropow: ${errorMessage(error)}`);
    } finally {
      setGenerating(false);
    }
  }

  function clearSelection() {
    setSelected(new Set());
    setAnchorArtifact(null);
  }

  function handleCropClick(event: MouseEvent<HTMLButtonElement>, crop: IdentityCropReviewCrop) {
    const result = updateCropSelection(
      orderedCrops,
      selected,
      crop.artifact,
      anchorArtifact,
      { shift: event.shiftKey, additive: event.metaKey || event.ctrlKey },
    );
    setSelected(result.selected);
    setAnchorArtifact(result.anchor);
  }

  async function apply(status: IdentityCropAssignmentStatus, playerId?: string) {
    if (selected.size === 0) return;
    if (status === 'assigned' && !playerId) {
      onStatus('Wybierz zawodnika przed przypisaniem cropow.');
      return;
    }
    setSaving(true);
    try {
      const updates: IdentityCropReviewUpdate[] = [...selected].map((artifact) => ({
        artifact,
        status,
        player_id: status === 'assigned' ? playerId : null,
      }));
      const data = await saveIdentityCropReview(match.id, updates);
      setReview(data);
      clearSelection();
      await onSaved();
      onStatus(`Zapisano ${updates.length} cropow. Pozostalo: ${data.summary.remaining}.`);
    } catch (error) {
      onStatus(`Nie udalo sie zapisac cropow: ${errorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    if (match.analysis_report?.status === 'completed') void load();
    else setReview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.analysis_report?.status]);

  useEffect(() => {
    clearSelection();
    setSimilaritySeedArtifact(null);
    setVisibleLimit(PAGE_SIZE);
  }, [queueFilter, teamFilter]);

  if (match.analysis_report?.status !== 'completed') return null;

  return (
    <section className='identity-crop-review-panel'>
      <div className='row between'>
        <div>
          <h3>Galeria identyfikacji</h3>
          <div className='chips'>
            <span>Pozostalo: {review?.summary.remaining ?? 'n/a'}</span>
            <span>Oznaczone: {review?.summary.reviewed ?? 'n/a'}</span>
            <span>Stinty automatyczne: {review?.summary.derived_stints ?? 0}</span>
          </div>
        </div>
        {review ? (
          <button type='button' onClick={() => setOpen(true)} disabled={loading}>
            Otworz galerie cropow
          </button>
        ) : (
          <div className='row'>
            <button type='button' onClick={() => void generate()} disabled={loading || generating}>
              {generating ? 'Generowanie...' : 'Wygeneruj cropy'}
            </button>
          </div>
        )}
      </div>

      {open && review && (
        <div className='identity-crop-review-modal' role='dialog' aria-modal='true'>
          <div className='identity-crop-review-shell'>
            <header className='identity-crop-review-toolbar'>
              <div>
                <h3>Galeria identyfikacji</h3>
                <p className='muted'>Pozostalo {review.summary.remaining} z {review.summary.crops_total}</p>
              </div>
              <label className='compact-field'>
                Druzyna
                <select
                  value={teamFilter}
                  onChange={(event) => setTeamFilter(event.target.value as TeamFilter)}
                >
                  <option value='A'>Team A</option>
                  <option value='B'>Team B</option>
                  <option value='U'>Unknown</option>
                  <option value='all'>Wszystkie</option>
                </select>
              </label>
              <label className='compact-field'>
                Widok
                <select
                  value={queueFilter}
                  onChange={(event) => setQueueFilter(event.target.value as QueueFilter)}
                >
                  <option value='remaining'>Pozostale</option>
                  <option value='assigned'>Przypisane</option>
                  <option value='flagged'>Odrzucone / niepewne</option>
                  <option value='all'>Wszystkie</option>
                </select>
              </label>
              <label className='identity-crop-player-field'>
                Zawodnik
                <select value={selectedPlayerId} onChange={(event) => setSelectedPlayerId(event.target.value)}>
                  <option value=''>Wybierz zawodnika</option>
                  {filteredRoster.map((player) => (
                    <option value={player.id} key={player.id}>
                      {player.number ? `#${player.number} ` : ''}{player.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type='button'
                onClick={() => void apply('assigned', selectedPlayerId)}
                disabled={saving || selected.size === 0 || !selectedPlayerId}
              >
                Przypisz ({selected.size})
              </button>
              <button type='button' className='secondary' onClick={() => void apply('wrong_team')} disabled={saving || selected.size === 0}>
                Inna druzyna
              </button>
              <button type='button' className='secondary' onClick={() => void apply('false_positive')} disabled={saving || selected.size === 0}>
                Bledna detekcja
              </button>
              <button type='button' className='secondary' onClick={() => void apply('unknown')} disabled={saving || selected.size === 0}>
                Nie wiem
              </button>
              {queueFilter !== 'remaining' && (
                <button type='button' className='secondary' onClick={() => void apply('unassigned')} disabled={saving || selected.size === 0}>
                  Cofnij
                </button>
              )}
              <button type='button' className='secondary' onClick={() => setOpen(false)} disabled={saving}>
                Zamknij
              </button>
            </header>

            <div className='identity-crop-review-status row between'>
              <span>{filteredCrops.length} cropow w widoku</span>
              <span>Zaznaczone: {selectedVisible}</span>
              <button
                type='button'
                className='secondary'
                onClick={() => {
                  const [artifact] = [...selected];
                  setSimilaritySeedArtifact(artifact || null);
                  setVisibleLimit(PAGE_SIZE);
                }}
                disabled={selected.size !== 1}
              >
                Sortuj podobne
              </button>
              {similaritySeedArtifact && (
                <button
                  type='button'
                  className='secondary'
                  onClick={() => setSimilaritySeedArtifact(null)}
                >
                  Kolejnosc czasowa
                </button>
              )}
              <button type='button' className='secondary' onClick={clearSelection} disabled={selected.size === 0}>
                Wyczysc zaznaczenie
              </button>
            </div>

            <div className='identity-crop-inbox-grid'>
              {renderedCrops.map((crop) => (
                <button
                  type='button'
                  className={selected.has(crop.artifact) ? 'identity-inbox-crop selected' : 'identity-inbox-crop'}
                  key={crop.artifact}
                  onClick={(event) => handleCropClick(event, crop)}
                >
                  <img loading='lazy' src={artifactUrl(match.id, crop.artifact)} alt={`${crop.stable_player_id} ${cropTime(crop)}`} />
                  <span>{cropTime(crop)} · {crop.stable_player_id}</span>
                  {similaritySeed && crop.artifact !== similaritySeed.artifact && (
                    <span>
                      podobienstwo: {(() => {
                        const distance = cropSimilarityDistance(similaritySeed, crop);
                        return distance === null ? 'brak danych' : `${Math.max(0, 100 - distance * 45).toFixed(0)}%`;
                      })()}
                    </span>
                  )}
                  {crop.player_name && <strong>{crop.player_name}</strong>}
                </button>
              ))}
            </div>

            {renderedCrops.length < filteredCrops.length && (
              <button
                type='button'
                className='secondary identity-load-more'
                onClick={() => setVisibleLimit((current) => current + PAGE_SIZE)}
              >
                Pokaz kolejne ({filteredCrops.length - renderedCrops.length})
              </button>
            )}
            {filteredCrops.length === 0 && <p className='empty-state'>Brak cropow w tym widoku.</p>}
          </div>
        </div>
      )}
    </section>
  );
}
