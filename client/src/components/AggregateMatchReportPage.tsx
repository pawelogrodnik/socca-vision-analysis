import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getMatchGroupReport } from '../api';
import { errorMessage } from '../lib/helpers';
import type { AggregatePublicMatchReport } from '../types';

function duration(seconds: number): string {
  const value = Math.max(0, Math.round(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function AggregateMatchReportPage() {
  const { groupId } = useParams();
  const [report, setReport] = useState<AggregatePublicMatchReport | null>(null);
  const [status, setStatus] = useState('');
  useEffect(() => {
    if (!groupId) return;
    void getMatchGroupReport(groupId).then(setReport).catch((error: unknown) => setStatus(errorMessage(error)));
  }, [groupId]);
  return <main className='app'>
    <section className='hero compact-hero'>
      <p className='eyebrow'>Scalony raport</p>
      <h1>{report?.match.title || 'Raport łączony'}</h1>
      <p>Osobny raport logicznego meczu. Nie zmienia żadnego z raportów źródłowych.</p>
      <Link to='/match-groups'>Scalone raporty</Link>
    </section>
    {status && <p className='status'>{status}</p>}
    {!report && !status && <p className='loading-line'>Ładuję scalony raport…</p>}
    {report && <>
      <section className='panel'><h2>Podsumowanie</h2><p>Łączny analizowany czas: <strong>{duration(report.timing.analyzed_duration_sec)}</strong></p>
        <p>Piłka i momentum: <strong>{report.stats_semantics?.ball || 'not_available'}</strong>{report.timelines?.attacking_momentum?.product_readiness === 'experimental' ? ' (eksperymentalne)' : ''}</p>
      </section>
      <section className='panel'><h2>Źródłowe fragmenty</h2>
        <ol>{report.sources.map((source) => <li key={source.published_id}><Link to={`/published/matches/${encodeURIComponent(source.published_id)}/report`}>Fragment {source.sequence_index + 1}</Link> · {duration((report.sources[source.sequence_index + 1]?.logical_offset_sec ?? report.timing.analyzed_duration_sec) - source.logical_offset_sec)}</li>)}</ol>
      </section>
      <section className='panel'><h2>Dane przestrzenne</h2><p>Heatmapy: {report.spatial.heatmaps.status} — {report.spatial.heatmaps.reason || 'brak bezpiecznej wspólnej orientacji'}.</p><p>Team Shape: {report.spatial.team_shape.status} — {report.spatial.team_shape.reason || 'brak bezpiecznej wspólnej orientacji'}.</p></section>
    </>}
  </main>;
}
