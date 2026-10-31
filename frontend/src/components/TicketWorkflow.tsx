import type { TicketDetailResponse } from '../api/cs';

type State = TicketDetailResponse['data']['workflow_state'];
const states: Record<State, { label: string; advice: string }> = {
  RECEIVED: { label: '접수', advice: '원문과 고객·제품 정보를 확인한 뒤 검토 시작 여부를 판단하세요.' },
  IN_REVIEW: { label: '검토 중', advice: '확인되지 않은 정보와 검토 근거를 점검하세요.' },
  WAITING_CUSTOMER: { label: '고객 회신 대기', advice: '고객 회신 수신 여부와 보완 정보를 확인하세요.' },
  ANSWERED: { label: '회신 완료', advice: '발송 이력과 처리 결과를 확인한 뒤 종결 가능 여부를 검토하세요.' },
  TRANSFERRED: { label: '부서 이관', advice: '담당 부서의 처리 결과와 검토 여부를 확인하세요.' },
  CLOSED: { label: '종결', advice: '종결 이력을 확인하세요. 새 문의는 별도로 검토하세요.' },
};

export default function TicketWorkflow({ state }: { state: unknown }) {
  const info = typeof state === 'string' && Object.hasOwn(states, state) ? states[state as State] : undefined;
  // 문의 처리 화면의 "다음 할 일" 줄 안에서 한 줄로 이어 읽히도록 span으로 낸다.
  return <>
    <span>업무 상태: <strong>{info?.label ?? '확인 필요'}</strong></span>
    <span className="sep">|</span>
    <span>다음 작업 안내: {info?.advice ?? '현재 상태를 확인할 수 없습니다. 최신 상태를 다시 조회하세요.'}</span>
  </>;
}
