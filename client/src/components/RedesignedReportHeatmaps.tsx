import type { PublicReportPlayer } from '../types';
import { displayJerseyNumber } from '../lib/publicReportPresentation';
import { PublicPlayerHeatmap } from './PublicPlayerHeatmap';

type Props = {
  players: PublicReportPlayer[];
  assetHref: (path: string) => string;
};

function playerName(player: PublicReportPlayer): string {
  const number = displayJerseyNumber(player.player_number);
  return `${number ? `#${number} ` : ''}${player.player_name || player.player_id}`;
}

export function RedesignedReportHeatmaps({ players, assetHref }: Props) {
  const heatmapPlayers = players.filter((player) => Boolean(player.heatmap?.interactive?.points.length || player.heatmap?.path));
  if (!heatmapPlayers.length) return null;

  return (
    <section className='redesign-section redesign-heatmaps-section' aria-labelledby='redesign-heatmaps-title'>
      <div className='redesign-section-heading'>
        <div><p className='redesign-kicker'>Pozycje na boisku</p><h2 id='redesign-heatmaps-title'>Heatmapy zawodników</h2></div>
      </div>
      <div className='redesign-heatmap-grid'>
        {heatmapPlayers.map((player) => (
          <article className='redesign-heatmap-card' key={player.player_id}>
            <h3>{playerName(player)}</h3>
            <PublicPlayerHeatmap alt={`Heatmapa ${playerName(player)}`} heatmap={player.heatmap} fallbackSrc={player.heatmap?.path ? assetHref(player.heatmap.path) : undefined} />
          </article>
        ))}
      </div>
    </section>
  );
}
