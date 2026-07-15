import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { AttackingMomentumPoint } from '../types';

type AttackingMomentumChartProps = {
  points: AttackingMomentumPoint[];
  teamAName: string;
  teamBName: string;
  teamAColor?: string;
  teamBColor?: string;
  quality?: string;
  warnings?: string[];
  compact?: boolean;
};

type TooltipRow = {
  payload?: AttackingMomentumPoint;
};

type MomentumTooltipProps = {
  active?: boolean;
  payload?: readonly TooltipRow[];
  teamAName: string;
  teamBName: string;
};

const DEFAULT_TEAM_A_COLOR = '#f8fafc';
const DEFAULT_TEAM_B_COLOR = '#38bdf8';

export function AttackingMomentumChart({
  points,
  teamAName,
  teamBName,
  teamAColor = DEFAULT_TEAM_A_COLOR,
  teamBColor = DEFAULT_TEAM_B_COLOR,
  quality,
  warnings = [],
  compact = false,
}: AttackingMomentumChartProps) {
  if (!points.length) return null;

  const tickInterval = Math.max(0, Math.ceil(points.length / 10) - 1);
  return (
    <div className={`attacking-momentum${compact ? ' compact' : ''}`}>
      <div className='row between attacking-momentum-heading'>
        <div>
          <h3>Momentum (experimental)</h3>
          <p className='muted'>
            Eksperymentalna estymacja nacisku ofensywnego. Nad osia: {teamAName}. Pod osia:{' '}
            {teamBName}.
          </p>
        </div>
        {quality && <span className={`confidence-pill ${quality}`}>jakosc: {quality}</span>}
      </div>
      <div className={compact ? 'momentum-chart compact' : 'momentum-chart'}>
        <ResponsiveContainer width='100%' height='100%'>
          <AreaChart data={points} margin={{ top: 12, right: 20, left: 0, bottom: 8 }}>
            <CartesianGrid stroke='#334155' strokeDasharray='3 3' vertical={false} />
            <XAxis
              dataKey='time_sec'
              interval={tickInterval}
              minTickGap={24}
              stroke='#94a3b8'
              tickFormatter={formatClock}
            />
            <YAxis domain={[-100, 100]} stroke='#94a3b8' tickFormatter={(value) => `${Number(value)}`} />
            <ReferenceLine y={0} stroke='#e2e8f0' strokeWidth={1.5} />
            <Tooltip
              content={({ active, payload }) => (
                <MomentumTooltip
                  active={active}
                  payload={payload as readonly TooltipRow[] | undefined}
                  teamAName={teamAName}
                  teamBName={teamBName}
                />
              )}
            />
            <Legend />
            <Area
              type='linear'
              dataKey='team_a_value'
              name={teamAName}
              stroke={teamAColor}
              fill={teamAColor}
              fillOpacity={0.82}
              baseValue={0}
              isAnimationActive={false}
            />
            <Area
              type='linear'
              dataKey='team_b_value'
              name={teamBName}
              stroke={teamBColor}
              fill={teamBColor}
              fillOpacity={0.82}
              baseValue={0}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {warnings.length > 0 && (
        <div className='momentum-warnings'>
          {warnings.map((warning) => (
            <span key={warning}>{warning}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function MomentumTooltip({ active, payload, teamAName, teamBName }: MomentumTooltipProps) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  const dominant = point.dominant_team_label === 'A'
    ? teamAName
    : point.dominant_team_label === 'B'
      ? teamBName
      : 'brak wyraznej przewagi';
  return (
    <div className='public-chart-tooltip momentum-tooltip'>
      <strong className='public-chart-tooltip-title'>{formatClock(point.time_sec)}</strong>
      <div className='public-chart-tooltip-list'>
        <div className='public-chart-tooltip-row'>
          <span className='public-chart-tooltip-name'>Przewaga</span>
          <strong>{dominant}</strong>
        </div>
        <div className='public-chart-tooltip-row'>
          <span className='public-chart-tooltip-name'>Score</span>
          <strong>{point.signed_score.toFixed(1)}</strong>
        </div>
        <div className='public-chart-tooltip-row'>
          <span className='public-chart-tooltip-name'>Pewnosc</span>
          <strong>{formatPercent(point.confidence)}</strong>
        </div>
        <div className='public-chart-tooltip-row'>
          <span className='public-chart-tooltip-name'>Pokrycie kontroli</span>
          <strong>{formatPercent(point.controlled_coverage)}</strong>
        </div>
      </div>
    </div>
  );
}

function formatClock(value: number): string {
  const seconds = Math.max(0, Math.round(Number(value) || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function formatPercent(value: number | undefined): string {
  return value == null ? '--' : `${(value * 100).toFixed(0)}%`;
}
