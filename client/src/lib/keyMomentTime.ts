export function parseKeyMomentTime(value: string): number | null {
  const trimmed = value.trim();
  if (/^\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  const match = /^(\d+):(\d{2}(?:\.\d+)?)$/.exec(trimmed);
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

export function formatKeyMomentTime(value: number): string {
  const safe = Math.max(0, value);
  return `${Math.floor(safe / 60)}:${String(Math.floor(safe % 60)).padStart(2, '0')}.${Math.round((safe % 1) * 10)}`;
}
