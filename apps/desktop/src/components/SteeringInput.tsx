/**
 * R5.1 compatibility shim.
 *
 * Live steering now uses the main Composer for every active session. Keeping a
 * second input here created two competing controls for automation-run sessions
 * and made "Steer" / "Follow-up" look distinct while both were routed through
 * the same user-message path. The export stays temporarily so the stacked R5.1
 * UI diff can be simplified without making App.tsx carry another unrelated
 * structural edit.
 */

export interface SteeringInputProps {
  active: boolean;
  onSteer: (text: string) => void;
  onFollowUp: (text: string) => void;
  onCancel: () => void;
}

export function SteeringInput(_props: SteeringInputProps) {
  return null;
}
