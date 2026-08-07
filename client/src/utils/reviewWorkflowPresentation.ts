import type { ReviewWorkflow } from '../types';

export function requiredCasesLabel(count: number): string {
  const safeCount = Math.max(0, Math.trunc(count));
  if (safeCount === 1) return '1 przypadek wymaga sprawdzenia';
  const endsWithTwoToFour = safeCount % 10 >= 2 && safeCount % 10 <= 4;
  const isTeen = safeCount % 100 >= 12 && safeCount % 100 <= 14;
  if (endsWithTwoToFour && !isTeen) return `${safeCount} przypadki wymagają sprawdzenia`;
  return `${safeCount} przypadków wymaga sprawdzenia`;
}

export function reviewWorkflowOperatorCopy(workflow: ReviewWorkflow | null): string {
  if (!workflow) return 'Ładowanie statusu Review…';
  if (workflow.review_complete) return 'Review zakończony';
  switch (workflow.required_action?.type) {
    case 'identify_players':
      return 'Rozpoznaj zawodników';
    case 'review_identity_issue':
      return requiredCasesLabel(workflow.issues.blocking);
    case 'finalize_identity':
      return 'Gotowe do przygotowania wideo';
    case 'wait_for_render':
      return 'Przygotowywanie wideo…';
    case 'approve_video_qa':
      return 'Wideo czeka na zatwierdzenie';
    case 'retry_render':
      return 'Generowanie wideo wymaga ponowienia';
    case 'retry_review_recompute':
      return 'Review wymaga odświeżenia';
    default:
      return workflow.status === 'error'
        ? 'Review wymaga sprawdzenia'
        : 'Review w toku';
  }
}

export function reportWorkflowOperatorCopy(workflow: ReviewWorkflow | null): string {
  return workflow?.can_enter_report ? 'Gotowe do raportu' : 'Najpierw zakończ Review';
}
