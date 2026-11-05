import { ElloWorking } from '../components/Ello';

// RFP 처리 상태 표시. C/S와 같은 엘로를 쓴다(2026-11-05 사용자 결정: AI 표시 통일).
export type RfpPersonaState = 'idle' | 'thinking' | 'speaking';
export interface RfpPersonaProps { state: RfpPersonaState; paused: boolean; label: string; completed?: boolean }

export function RfpPersona({ state, paused, label, completed = false }: RfpPersonaProps) {
  return <span className="rfp-persona" role="img" aria-label={label} data-state={state} data-paused={paused} data-completed={completed}>
    <ElloWorking size={40} still={paused || completed} />
  </span>;
}
