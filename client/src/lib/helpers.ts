import type { Team } from '../types';

type Point = [number, number];

export const pretty = (value: unknown) => JSON.stringify(value, null, 2);

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function parseRoster(value: string) {
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

export function rosterToText(
  players: { name: string; number?: string | null; role?: string }[],
) {
  return players
    .map((player) =>
      [player.name, player.number || '', player.role || 'player'].join(', '),
    )
    .join('\n');
}

export function emptyTeam(name: string, color: string): Team {
  return {
    name,
    color,
    players: [],
  };
}

export function defaultTeams(): Team[] {
  return [emptyTeam('Team A', '#ef4444'), emptyTeam('Team B', '#2563eb')];
}

export function drawPitchOverlay(
  ctx: CanvasRenderingContext2D,
  points: Point[],
) {
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
