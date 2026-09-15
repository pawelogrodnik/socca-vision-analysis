import { useEffect, useState } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import {
  acceptKeyMomentSuggestion,
  acceptShotReviewSuggestionCluster,
  createShotReviewShot,
  deleteShotReviewShot,
  editShotReviewShot,
  getKeyMomentEditor,
  getShotReviewEditor,
  getMergedMatchExternalVideo,
  getPublishedMatch,
  getStaticPublicMatchReport,
  saveKeyMomentEditor,
  rejectKeyMomentSuggestion,
  rejectShotReviewSuggestionCluster,
} from '../api';
import { errorMessage } from '../lib/helpers';
import { ApiRequestError } from '../lib/apiErrors';
import type { KeyMomentEditorState, MatchGroupExternalVideoStatus, PublicMatchReport, PublishedMatchDetail, ShotReviewEditorState } from '../types';
import { MergedSourceDataRebuildPanel } from './MergedSourceDataRebuildPanel';
import { BallAnalysisRebuildPanel } from './BallAnalysisRebuildPanel';
import { RedesignedPublishedReportContent } from './RedesignedPublishedReportContent';

export function RedesignedPublishedMatchReportPage() {
  const { matchId } = useParams();
  const location = useLocation();
  const devPresentation = new URLSearchParams(location.search).get('dev') === '1';
  const [report, setReport] = useState<PublicMatchReport | null>(null);
  const [externalVideo, setExternalVideo] = useState<MatchGroupExternalVideoStatus | null>(null);
  const [externalVideoLoading, setExternalVideoLoading] = useState(true);
  const [editor, setEditor] = useState<KeyMomentEditorState | null>(null);
  const [shotReviewEditor, setShotReviewEditor] = useState<ShotReviewEditorState | null>(null);
  const [ballAnalysisRefresh, setBallAnalysisRefresh] = useState(0);
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

  useEffect(() => {
    if (!devPresentation || !matchId) { setShotReviewEditor(null); return; }
    let cancelled = false;
    void getShotReviewEditor(matchId)
      .then((value) => { if (!cancelled) setShotReviewEditor(value); })
      .catch(() => { if (!cancelled) setShotReviewEditor(null); });
    return () => { cancelled = true; };
  }, [devPresentation, matchId, report?.id]);

  async function reloadShotReviewAfterConflict(error: unknown): Promise<never> {
    if (!(error instanceof ApiRequestError) || error.code !== 'shot_review_revision_conflict' || !matchId) throw error;
    const latest = await getShotReviewEditor(matchId);
    setShotReviewEditor(latest);
    throw new Error('Stan Shot Review zmienił się. Odświeżono najnowszą wersję.');
  }

  return <main className='redesigned-report-shell'>
    {status ? <p className='status'>{status}</p> : null}
    {!report && !status ? <p className='loading-line'><span className='spinner' />Ładuję raport...</p> : null}
    {report ? <RedesignedPublishedReportContent
      report={report}
      externalVideo={externalVideo}
      externalVideoLoading={externalVideoLoading}
      editorState={devPresentation ? editor : null}
      onSaveEditor={async (draft) => {
        if (!matchId) throw new Error('Brak identyfikatora publikacji.');
        const saved = await saveKeyMomentEditor(matchId, draft);
        setEditor(saved); if (saved.public_report) setReport(saved.public_report); return saved;
      }}
      onAcceptSuggestion={async (payload) => {
        if (!matchId) throw new Error('Brak identyfikatora publikacji.');
        const saved = await acceptKeyMomentSuggestion(matchId, payload);
        setEditor(saved); if (saved.public_report) setReport(saved.public_report); return saved;
      }}
      onRejectSuggestion={async (payload) => {
        if (!matchId) throw new Error('Brak identyfikatora publikacji.');
        const saved = await rejectKeyMomentSuggestion(matchId, payload);
        setEditor(saved); return saved;
      }}
      shotReviewState={devPresentation ? shotReviewEditor : null}
      onCreateShot={async (payload) => {
        if (!matchId) throw new Error('Brak identyfikatora publikacji.');
        try {
          const saved = await createShotReviewShot(matchId, payload);
          setShotReviewEditor(saved); setBallAnalysisRefresh((value) => value + 1); return saved;
        } catch (error) { return reloadShotReviewAfterConflict(error); }
      }}
      onEditShot={async (shotId, payload) => {
        if (!matchId) throw new Error('Brak identyfikatora publikacji.');
        try {
          const saved = await editShotReviewShot(matchId, shotId, payload);
          setShotReviewEditor(saved); setBallAnalysisRefresh((value) => value + 1); return saved;
        } catch (error) { return reloadShotReviewAfterConflict(error); }
      }}
      onDeleteShot={async (shotId, payload) => {
        if (!matchId) throw new Error('Brak identyfikatora publikacji.');
        try {
          const saved = await deleteShotReviewShot(matchId, shotId, payload);
          setShotReviewEditor(saved); setBallAnalysisRefresh((value) => value + 1); return saved;
        } catch (error) { return reloadShotReviewAfterConflict(error); }
      }}
      onAcceptShotSuggestionCluster={async (payload) => {
        if (!matchId) throw new Error('Brak identyfikatora publikacji.');
        try {
          const saved = await acceptShotReviewSuggestionCluster(matchId, payload);
          setShotReviewEditor(saved); setBallAnalysisRefresh((value) => value + 1); return saved;
        } catch (error) { return reloadShotReviewAfterConflict(error); }
      }}
      onRejectShotSuggestionCluster={async (payload) => {
        if (!matchId) throw new Error('Brak identyfikatora publikacji.');
        try {
          const saved = await rejectShotReviewSuggestionCluster(matchId, payload);
          setShotReviewEditor(saved); setBallAnalysisRefresh((value) => value + 1); return saved;
        } catch (error) { return reloadShotReviewAfterConflict(error); }
      }}
      sourceDataRebuildPanel={
        devPresentation && matchId ? <>
          <BallAnalysisRebuildPanel
            publishedMatchId={matchId}
            devAllowed
            refreshKey={ballAnalysisRefresh}
            onRebuilt={() => {
              void getPublishedMatch(matchId).then((updated) => {
                if (updated.public_report) setReport(updated.public_report);
              }).catch(() => undefined);
            }}
          />
          {report.merged_provenance ? <MergedSourceDataRebuildPanel
            mergedId={matchId}
            devAllowed
            onReportUpdated={(updated: PublishedMatchDetail) => {
              if (updated.public_report) setReport(updated.public_report);
            }}
          /> : null}
        </> : null
      }
    /> : null}
  </main>;
}
