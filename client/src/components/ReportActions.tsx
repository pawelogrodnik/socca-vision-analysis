import { useState } from 'react';
import { useLocation } from 'react-router-dom';

type ReportAction = 'package' | 'publish' | 'replace' | null;

type JsonDownload = {
  label: string;
  filename: string;
  data: unknown;
};

type ReportActionsProps = {
  mode: 'local' | 'published';
  packageHref?: string;
  publicReportPath?: string;
  jsonDownload?: JsonDownload;
  busyAction?: ReportAction;
  status?: string;
  onBuildPackage?: () => Promise<void> | void;
  onPublish?: () => Promise<void> | void;
  onReplacePublish?: () => Promise<void> | void;
};

function absoluteUrl(path: string): string {
  const origin = globalThis.location?.origin || '';
  return `${origin}${path}`;
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}

function downloadJson({ filename, data }: JsonDownload): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function ReportActions({
  mode,
  packageHref,
  publicReportPath,
  jsonDownload,
  busyAction = null,
  status,
  onBuildPackage,
  onPublish,
  onReplacePublish,
}: ReportActionsProps) {
  const location = useLocation();
  const [copyStatus, setCopyStatus] = useState('');
  const currentUrl = absoluteUrl(`${location.pathname}${location.search}`);
  const publicUrl = publicReportPath ? absoluteUrl(publicReportPath) : '';
  const isBusy = busyAction !== null;

  async function copy(url: string, label: string) {
    try {
      await copyText(url);
      setCopyStatus(`Skopiowano ${label}.`);
    } catch (error) {
      setCopyStatus(`Nie udalo sie skopiowac: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <section className='card report-actions'>
      <div className='row between'>
        <div>
          <h2>Udostepnianie i eksport</h2>
          <p className='muted'>
            Raport jest tracking-only. Eksport zachowuje snapshot danych bez video.
          </p>
        </div>
        <span className='confidence-pill'>{mode === 'published' ? 'Published snapshot' : 'Local draft'}</span>
      </div>

      <div className='report-actions-main'>
        <button type='button' onClick={() => void copy(currentUrl, 'link do raportu')}>
          Kopiuj link
        </button>
        {publicUrl && (
          <button type='button' onClick={() => void copy(publicUrl, 'link publiczny')}>
            Kopiuj publiczny link
          </button>
        )}
        <button type='button' className='secondary' onClick={() => globalThis.print()}>
          Drukuj / PDF
        </button>
        {jsonDownload && (
          <button type='button' className='secondary' onClick={() => downloadJson(jsonDownload)}>
            {jsonDownload.label}
          </button>
        )}
        {packageHref && (
          <a className='button-like secondary' href={packageHref} download>
            Pobierz match_package.json
          </a>
        )}
      </div>

      {(onBuildPackage || onPublish || onReplacePublish) && (
        <div className='report-actions-main'>
          {onBuildPackage && (
            <button type='button' onClick={onBuildPackage} disabled={isBusy}>
              {busyAction === 'package' ? 'Generuje...' : 'Generuj package'}
            </button>
          )}
          {onPublish && (
            <button type='button' onClick={onPublish} disabled={isBusy}>
              {busyAction === 'publish' ? 'Publikuje...' : 'Publikuj do SQLite'}
            </button>
          )}
          {onReplacePublish && (
            <button type='button' className='secondary' onClick={onReplacePublish} disabled={isBusy}>
              {busyAction === 'replace' ? 'Nadpisuje...' : 'Nadpisz w SQLite'}
            </button>
          )}
        </div>
      )}

      {(copyStatus || status) && (
        <p className='report-action-status'>
          {busyAction && <span className='spinner' />}
          {status || copyStatus}
        </p>
      )}
    </section>
  );
}
