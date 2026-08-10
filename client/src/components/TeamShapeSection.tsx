import type { TeamShapeDocument } from '../types';

type TeamShapeSectionProps = {
  teamShape?: TeamShapeDocument | null;
};

function formatMetric(value: number | undefined): string {
  return value == null ? '--' : `${value.toFixed(1)} m`;
}

function formatCount(value: number | undefined): string {
  return value == null ? '--' : String(Math.round(value));
}

export function TeamShapeSection({ teamShape }: TeamShapeSectionProps) {
  if (!teamShape?.available || !teamShape.teams?.length) return null;

  return (
    <section className='card'>
      <h2>Team Shape</h2>
      <p className='muted'>Opis przestrzennego ustawienia drużyny w kolejnych fragmentach meczu.</p>
      <div className='team-shape-grid'>
        {teamShape.teams.map((team) => (
          <article className='team-shape-card' key={team.team_label || team.team_id || team.team_name}>
            <h3>{team.team_name || `Team ${team.team_label || ''}`}</h3>
            <div className='team-shape-metrics'>
              <span>Szerokość: {formatMetric(team.summary?.width_m)}</span>
              <span>Głębokość: {formatMetric(team.summary?.depth_m)}</span>
              <span>Kompaktowość: {formatMetric(team.summary?.compactness_m)}</span>
              <span>Block height: {formatMetric(team.summary?.block_height_m)}</span>
              <span>Próbki: {formatCount(team.summary?.sample_count)}</span>
            </div>
            {team.summary?.takeaways?.length ? (
              <ul>
                {team.summary.takeaways.map((takeaway) => (
                  <li key={takeaway}>{takeaway}</li>
                ))}
              </ul>
            ) : null}
            {team.timeline?.length ? (
              <div className='team-shape-timeline'>
                <strong>Timeline</strong>
                <ul>
                  {team.timeline.map((point) => (
                    <li key={`${team.team_label || team.team_name}-${point.label || point.minute}`}>
                      {point.label || point.minute || '--'}: {formatMetric(point.width_m)} / {formatMetric(point.depth_m)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </article>
        ))}
      </div>
      {teamShape.takeaways?.length ? (
        <div className='team-shape-takeaways'>
          <strong>Wnioski</strong>
          <ul>
            {teamShape.takeaways.map((takeaway) => (
              <li key={takeaway}>{takeaway}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
