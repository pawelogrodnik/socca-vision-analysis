import { useState } from 'react';
import type { PublicReportPlayer } from '../types';
import { displayJerseyNumber } from '../lib/publicReportPresentation';
import { PublicPlayerHeatmap } from './PublicPlayerHeatmap';

type Props = { players: PublicReportPlayer[]; teamName?: string | null; assetHref: (path: string) => string };

function playerName(player: PublicReportPlayer): string {
  const number = displayJerseyNumber(player.player_number);
  return `${number ? `#${number} ` : ''}${player.player_name || player.player_id}`;
}

export function RedesignedReportHeatmaps({ players, teamName, assetHref }: Props) {
  const [selectedPlayerId, setSelectedPlayerId] = useState<string | null>(null);
  const heatmapPlayers = players.filter((player) => Boolean(player.heatmap?.interactive?.points.length || player.heatmap?.path));
  const selected = heatmapPlayers.find((player) => player.player_id === selectedPlayerId) || heatmapPlayers[0];
  if (!selected) return null;
  return <section className='redesign-section redesign-heatmaps-section' aria-labelledby='redesign-heatmaps-title'>
    <div className='redesign-section-heading'><div><p className='redesign-kicker'>Pozycje na boisku</p><h2 id='redesign-heatmaps-title'>Heatmapy zawodników</h2></div></div>
    <div className='redesign-selected-heatmap'>
      <div className='redesign-heatmap-player'><strong>{playerName(selected)}</strong><span>{teamName || ''}</span></div>
      <PublicPlayerHeatmap alt={`Heatmapa ${playerName(selected)}`} heatmap={selected.heatmap} fallbackSrc={selected.heatmap?.path ? assetHref(selected.heatmap.path) : undefined} />
      <div className='redesign-heatmap-selector' aria-label='Wybór heatmapy zawodnika'>{heatmapPlayers.map((player) => <button className={player.player_id === selected.player_id ? 'active' : ''} type='button' onClick={() => setSelectedPlayerId(player.player_id)} key={player.player_id}>{playerName(player)}</button>)}</div>
    </div>
  </section>;
}
