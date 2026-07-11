import { formatTimelineTime, possessionTimelineShares } from '../lib/possessionTimeline';
import type { PossessionReport, PossessionTimelinePoint } from '../types';

interface PossessionTimelineChartProps {
  report?: PossessionReport | null;
}

const chartWidth = 720;
const chartHeight = 132;
const plotTop = 12;
const plotBottom = 104;
const plotLeft = 18;
const plotRight = 702;

export function PossessionTimelineChart({ report }: PossessionTimelineChartProps) {
  const points = report?.possession_timeline || [];
  if (!points.length) return null;

  const plotHeight = plotBottom - plotTop;
  const barGap = points.length > 24 ? 2 : 4;
  const barWidth = Math.max(4, (plotRight - plotLeft) / points.length - barGap);
  const ticks = timelineTicks(points);

  return (
    <div className='possession-timeline-card'>
      <div className='row between'>
        <div>
          <h4>Possession timeline</h4>
          <p className='muted'>A/B per timestamp; gray means free, contested or unknown.</p>
        </div>
        <div className='possession-legend'>
          <span><i className='legend-a' />A</span>
          <span><i className='legend-b' />B</span>
          <span><i className='legend-other' />other</span>
        </div>
      </div>
      <svg className='possession-timeline-chart' viewBox={`0 0 ${chartWidth} ${chartHeight}`} role='img'>
        <line x1={plotLeft} y1={plotTop} x2={plotRight} y2={plotTop} className='grid-line' />
        <line x1={plotLeft} y1={(plotTop + plotBottom) / 2} x2={plotRight} y2={(plotTop + plotBottom) / 2} className='grid-line' />
        <line x1={plotLeft} y1={plotBottom} x2={plotRight} y2={plotBottom} className='grid-line' />
        {points.map((point, index) => {
          const shares = possessionTimelineShares(point);
          const x = plotLeft + index * ((plotRight - plotLeft) / points.length) + barGap / 2;
          const otherHeight = shares.other * plotHeight;
          const teamBHeight = shares.teamB * plotHeight;
          const teamAHeight = shares.teamA * plotHeight;
          const otherY = plotTop;
          const teamBY = otherY + otherHeight;
          const teamAY = teamBY + teamBHeight;
          return (
            <g key={`${point.index}-${point.start_time_sec}`}>
              <rect x={x} y={otherY} width={barWidth} height={otherHeight} className='possession-other' />
              <rect x={x} y={teamBY} width={barWidth} height={teamBHeight} className='possession-b' />
              <rect x={x} y={teamAY} width={barWidth} height={teamAHeight} className='possession-a' />
            </g>
          );
        })}
        {ticks.map((tick) => (
          <text x={tick.x} y={124} textAnchor={tick.anchor} key={`${tick.label}-${tick.x}`}>
            {tick.label}
          </text>
        ))}
      </svg>
    </div>
  );
}

function timelineTicks(points: PossessionTimelinePoint[]) {
  const indexes = Array.from(new Set([0, Math.floor((points.length - 1) / 2), points.length - 1]));
  return indexes.map((index) => {
    const point = points[index];
    const x = plotLeft + index * ((plotRight - plotLeft) / Math.max(1, points.length - 1));
    const anchor = index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle';
    return {
      x,
      label: formatTimelineTime(point.time_sec),
      anchor: anchor as 'start' | 'end' | 'middle',
    };
  });
}
