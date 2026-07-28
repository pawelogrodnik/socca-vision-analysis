import type {
  InitialIdentityAuditObservation,
  VideoMetadata,
} from '../types';
import { observationCropLayout } from '../utils/initialIdentityAudit';

interface IdentityAuditObservationPreviewProps {
  observation: InitialIdentityAuditObservation | null;
  video: VideoMetadata;
  frameArtifactUrl: string;
  emptyLabel: string;
}

function observationDescriptor(observation: InitialIdentityAuditObservation): string {
  const team = observation.team_label === 'U'
    ? 'Nieznana druzyna'
    : `Team ${observation.team_label}`;
  return `${team} · ${observation.role.replace(/_/g, ' ')}`;
}

export function IdentityAuditObservationPreview({
  observation,
  video,
  frameArtifactUrl,
  emptyLabel,
}: IdentityAuditObservationPreviewProps) {
  if (!observation) {
    return <p className='muted'>{emptyLabel}</p>;
  }

  const crop = observationCropLayout(observation, video);
  return (
    <div className='initial-identity-audit-crop-preview'>
      <div className='initial-identity-audit-crop-heading'>
        <strong>Wybrany zawodnik</strong>
        <span>{observationDescriptor(observation)}</span>
      </div>
      <div className='initial-identity-audit-crop-stage'>
        <div
          className='initial-identity-audit-crop-viewport'
          style={{ aspectRatio: crop.aspectRatio }}
        >
          <img
            src={frameArtifactUrl}
            alt='Powiekszenie wybranego zawodnika'
            style={crop.imageStyle}
          />
          <span
            aria-hidden='true'
            className='initial-identity-audit-crop-target'
            style={crop.targetBoxStyle}
          >
            Wybrany
          </span>
        </div>
      </div>
      <p className='initial-identity-audit-crop-help'>
        Turkusowy obrys wskazuje osobe, ktorej dotyczy decyzja.
      </p>
    </div>
  );
}
