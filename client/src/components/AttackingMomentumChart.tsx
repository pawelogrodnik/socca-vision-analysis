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
import { useId, useMemo } from 'react';
import type { AnalyticsWarning, AttackingMomentumPoint } from '../types';

type AttackingMomentumChartProps = {
  points: AttackingMomentumPoint[];
  teamAName: string;
  teamBName: string;
  teamAColor?: string;
  teamBColor?: string;
  quality?: string;
  warnings?: Array<string | AnalyticsWarning>;
  compact?: boolean;
  onPointSelect?: (point: AttackingMomentumPoint) => void;
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
  onPointSelect,
}: AttackingMomentumChartProps) {
  const gradientSuffix = useId().replace(/:/g, '');
  const teamAGradientId = `momentum-team-a-${gradientSuffix}`;
  const teamBGradientId = `momentum-team-b-${gradientSuffix}`;
  const chartPoints = useMemo(
    () => points.map((point) => ({
      ...point,
      team_a_value: point.dominant_team_label ? point.team_a_value : 0,
      team_b_value: point.dominant_team_label ? point.team_b_value : 0,
    })),
    [points],
  );
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
          <AreaChart
            data={chartPoints}
            margin={{ top: 12, right: 20, left: 0, bottom: 8 }}
            onClick={(state) => selectActivePoint(state, onPointSelect)}
          >
            <defs>
              <linearGradient id={teamAGradientId} x1='0' y1='0' x2='1' y2='0'>
                {confidenceStops(points, 'A', teamAColor)}
              </linearGradient>
              <linearGradient id={teamBGradientId} x1='0' y1='0' x2='1' y2='0'>
                {confidenceStops(points, 'B', teamBColor)}
              </linearGradient>
            </defs>
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
              fill={`url(#${teamAGradientId})`}
              fillOpacity={1}
              baseValue={0}
              isAnimationActive={false}
            />
            <Area
              type='linear'
              dataKey='team_b_value'
              name={teamBName}
              stroke={teamBColor}
              fill={`url(#${teamBGradientId})`}
              fillOpacity={1}
              baseValue={0}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {warnings.length > 0 && (
        <div className='momentum-warnings'>
          {warnings.map((warning, index) => (
            <span key={`${warningCode(warning)}-${index}`}>{warningMessage(warning)}</span>
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
          <span className='public-chart-tooltip-name'>Pozycja</span>
          <strong>{formatPercent(point.positional_confidence)}</strong>
        </div>
        <div className='public-chart-tooltip-row'>
          <span className='public-chart-tooltip-name'>Eventy</span>
          <strong>{formatPercent(point.event_confidence)}</strong>
        </div>
        <div className='public-chart-tooltip-row'>
          <span className='public-chart-tooltip-name'>Evidence</span>
          <strong>{evidenceLabel(point)}</strong>
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

function confidenceStops(points: AttackingMomentumPoint[], team: 'A' | 'B', color: string) {
  const denominator = Math.max(1, points.length - 1);
  return points.map((point, index) => {
    const isDominant = point.dominant_team_label === team;
    const opacity = isDominant ? 0.22 + Math.max(0, Math.min(1, point.confidence || 0)) * 0.7 : 0.06;
    return (
      <stop
        key={`${team}-${point.index}-${index}`}
        offset={`${(index / denominator) * 100}%`}
        stopColor={color}
        stopOpacity={opacity}
      />
    );
  });
}

function selectActivePoint(
  state: unknown,
  onPointSelect: ((point: AttackingMomentumPoint) => void) | undefined,
) {
  if (!onPointSelect || !state || typeof state !== 'object') return;
  const activePayload = (state as { activePayload?: Array<{ payload?: AttackingMomentumPoint }> }).activePayload;
  const point = activePayload?.[0]?.payload;
  if (point) onPointSelect(point);
}

function warningCode(warning: string | AnalyticsWarning): string {
  return typeof warning === 'string' ? 'legacy-warning' : warning.code;
}

function warningMessage(warning: string | AnalyticsWarning): string {
  return typeof warning === 'string' ? warning : warning.message;
}

function evidenceLabel(point: AttackingMomentumPoint): string {
  const evidence = point.evidence || {};
  const passes = Number(evidence.completed_passes || 0) + Number(evidence.failed_passes || 0);
  const restarts = Number(evidence.restart_passes || 0) + Number(evidence.restart_setup_bonuses || 0);
  return `pos ${point.team_a_controlled_samples || 0}/${point.team_b_controlled_samples || 0}, pass ${passes}, restart ${restarts}`;
}
