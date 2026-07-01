export type WorkflowStepStatus = 'current' | 'done' | 'ready' | 'locked';

export type WorkflowStep = {
  id: string;
  label: string;
  description: string;
  status: WorkflowStepStatus;
  statusLabel?: string;
  disabled?: boolean;
};

interface MatchWorkflowStepperProps {
  steps: WorkflowStep[];
  onSelect: (stepId: string) => void;
}

function statusLabel(status: WorkflowStepStatus): string {
  if (status === 'done') return 'done';
  if (status === 'current') return 'current';
  if (status === 'locked') return 'locked';
  return 'ready';
}

export function MatchWorkflowStepper({
  steps,
  onSelect,
}: MatchWorkflowStepperProps) {
  return (
    <nav className='workflow-stepper' aria-label='Match workflow'>
      {steps.map((step, index) => (
        <button
          type='button'
          key={step.id}
          className={`workflow-step ${step.status}`}
          disabled={step.disabled}
          onClick={() => onSelect(step.id)}
        >
          <span className='workflow-step-index'>{index + 1}</span>
          <span>
            <strong>{step.label}</strong>
            <small>{step.description}</small>
          </span>
          <em>{step.statusLabel || statusLabel(step.status)}</em>
        </button>
      ))}
    </nav>
  );
}
