import type { ReviewedCorrectionAction } from '../types';

export type TeamAttributionActionCard = {
  action: ReviewedCorrectionAction;
  label: string;
  description?: string;
};

export const TEAM_ATTRIBUTION_ONLY_ACTIONS: readonly TeamAttributionActionCard[] = [
  { action: 'referee', label: 'Sędzia' },
  { action: 'false_detection', label: 'Fałszywa detekcja' },
  { action: 'team_unknown', label: 'Nieznana drużyna' },
  { action: 'unresolved', label: 'Nie wiem' },
];
