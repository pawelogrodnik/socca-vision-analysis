import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { getMergedMatchForGroup } from '../api';
import { errorMessage } from '../lib/helpers';

/** Backward-compatibility redirect: the old aggregate-report URL resolves to the canonical merged published match. */
export function MatchGroupReportRedirect() {
  const { groupId } = useParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!groupId) {
      setStatus('Missing match group id.');
      return;
    }
    getMergedMatchForGroup(groupId)
      .then((result) => {
        navigate(`/published/matches/${encodeURIComponent(result.merged_published_match_id)}/report`, { replace: true });
      })
      .catch((error: unknown) => setStatus(errorMessage(error)));
  }, [groupId, navigate]);

  return <main className='app'>
    <section className='hero compact-hero'>
      <p className='eyebrow'>Scalony mecz</p>
      <h1>Przekierowanie do scalonego meczu…</h1>
      <p>Raporty łączone zostały zastąpione normalnymi raportami scalonych meczów.</p>
      <Link to='/match-groups'>Scalone mecze</Link>
    </section>
    {status
      ? <p className='status'>{status}</p>
      : <p className='loading-line'><span className='spinner' /> Szukam scalonego meczu…</p>}
  </main>;
}
