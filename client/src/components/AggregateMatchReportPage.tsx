import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getMatchGroupReport } from '../api';
import { errorMessage } from '../lib/helpers';
import type { AggregatePublicMatchReport, MatchGroupCompatibility } from '../types';
import { AggregateMatchReportContent } from './AggregateMatchReportContent';

function duration(seconds: number): string {
  const value = Math.max(0, Math.round(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function AggregateMatchReportPage() {
  const { groupId } = useParams();
  const [report, setReport] = useState<AggregatePublicMatchReport | null>(null);
  const [validation, setValidation] = useState<MatchGroupCompatibility | null>(null);
  const [status, setStatus] = useState('');
  useEffect(() => {
    if (!groupId) return;
    void getMatchGroupReport(groupId).then((response) => {
      setReport(response.report);
      setValidation(response.validation);
    }).catch((error: unknown) => setStatus(errorMessage(error)));
  }, [groupId]);
  return <main className='app'>
    <section className='hero compact-hero'>
      <p className='eyebrow'>Scalony raport</p>
      <h1>{report?.match.title || 'Raport łączony'}</h1>
      <p>Osobny raport logicznego meczu. Nie zmienia żadnego z raportów źródłowych.</p>
      <Link to='/match-groups'>Scalone raporty</Link>
    </section>
    {status && <p className='status'>{status}</p>}
    {validation && validation.status !== 'compatible' && <section className='status'>
      <strong>Raport jest nieaktualny.</strong> {validation.blocking_reasons[0]?.detail || 'Źródła wymagają ponownej weryfikacji.'}
    </section>}
    {!report && !status && <p className='loading-line'>Ładuję scalony raport…</p>}
    {report && <>
      <section className='panel'><h2>Podsumowanie</h2><p>Łączny analizowany czas: <strong>{duration(report.timing.analyzed_duration_sec)}</strong></p></section>
      <AggregateMatchReportContent report={report} />
    </>}
  </main>;
}
