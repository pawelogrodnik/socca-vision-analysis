import { useEffect, useRef } from 'react';

import type { Match, ReviewedIdentityCoverageDebt } from '../types';
import { ReviewedIdentityCoverageDebtSummary } from './ReviewedIdentityCoverageDebtSummary';

type Props = {
  match: Match;
  debt: ReviewedIdentityCoverageDebt;
  mixedLocked: boolean;
  onClose: () => void;
};

export function ReviewedIdentityCoverageDebtDialog({ match, debt, mixedLocked, onClose }: Props) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  return <div className='coverage-debt-dialog-backdrop' onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className='coverage-debt-dialog' role='dialog' aria-modal='true' aria-labelledby='coverage-debt-dialog-title'>
      <header>
        <h2 id='coverage-debt-dialog-title'>Szczegóły pokrycia rozpoznania</h2>
        <button ref={closeButtonRef} type='button' onClick={onClose} aria-label='Zamknij szczegóły pokrycia'>Zamknij</button>
      </header>
      <div className='coverage-debt-dialog-content'>
        <ReviewedIdentityCoverageDebtSummary match={match} debt={debt} mixedLocked={mixedLocked} />
      </div>
    </section>
  </div>;
}
