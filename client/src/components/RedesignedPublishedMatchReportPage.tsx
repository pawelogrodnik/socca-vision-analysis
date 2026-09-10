import { useEffect, useState } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import {
  getKeyMomentEditor,
  getMergedMatchExternalVideo,
  getPublishedMatch,
  getStaticPublicMatchReport,
  saveKeyMomentEditor,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type { KeyMomentEditorState, MatchGroupExternalVideoStatus, PublicMatchReport } from '../types';
import { KeyMomentsEditorModal } from './KeyMomentsEditorModal';
import { RedesignedPublishedReportContent } from './RedesignedPublishedReportContent';

export function RedesignedPublishedMatchReportPage() {
  const { matchId } = useParams();
  const location = useLocation();
  const devPresentation = new URLSearchParams(location.search).get('dev') === '1';
  const [report, setReport] = useState<PublicMatchReport | null>(null);
  const [externalVideo, setExternalVideo] = useState<MatchGroupExternalVideoStatus | null>(null);
  const [externalVideoLoading, setExternalVideoLoading] = useState(true);
  const [editor, setEditor] = useState<KeyMomentEditorState | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!matchId) { setStatus('Brak identyfikatora raportu.'); return; }
    let cancelled = false;
    setStatus('');
    getPublishedMatch(matchId)
      .then((detail) => detail.public_report || Promise.reject(new Error('Brak publicznego raportu.')))
      .catch(() => getStaticPublicMatchReport(matchId))
      .then((value) => { if (!cancelled) setReport(value); })
      .catch((error) => { if (!cancelled) setStatus(errorMessage(error)); });
    return () => { cancelled = true; };
  }, [matchId]);

  useEffect(() => {
    if (!matchId) { setExternalVideoLoading(false); return; }
    let cancelled = false;
    setExternalVideo(null);
    setExternalVideoLoading(true);
    void getMergedMatchExternalVideo(matchId)
      .then((value) => { if (!cancelled) setExternalVideo(value); })
      .catch(() => { if (!cancelled) setExternalVideo(null); })
      .finally(() => { if (!cancelled) setExternalVideoLoading(false); });
    return () => { cancelled = true; };
  }, [matchId]);

  useEffect(() => {
    if (!devPresentation || !matchId) { setEditor(null); return; }
    let cancelled = false;
    void getKeyMomentEditor(matchId)
      .then((value) => { if (!cancelled) setEditor(value); })
      .catch(() => { if (!cancelled) setEditor(null); });
    return () => { cancelled = true; };
  }, [devPresentation, matchId, report?.id]);

  return <main className='redesigned-report-shell'>
    {status ? <p className='status'>{status}</p> : null}
    {!report && !status ? <p className='loading-line'><span className='spinner' />Ładuję raport...</p> : null}
    {report ? <RedesignedPublishedReportContent
      report={report}
      externalVideo={externalVideo}
      externalVideoLoading={externalVideoLoading}
      editorAllowed={Boolean(editor?.key_moment_editor_allowed)}
      onEditKeyMoments={() => setEditorOpen(true)}
    /> : null}
    {editorOpen && editor?.key_moment_editor_allowed && report && matchId ? <KeyMomentsEditorModal
      state={editor}
      report={report}
      currentVideoTime={null}
      onClose={() => setEditorOpen(false)}
      onSave={async (draft) => {
        const saved = await saveKeyMomentEditor(matchId, draft);
        setEditor(saved);
        if (saved.public_report) setReport(saved.public_report);
        setEditorOpen(false);
      }}
    /> : null}
  </main>;
}
