import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode } from '../api/client';
import { getTicketWorkflow, isInvalidOwner, isVersionConflict, reassignOwner } from '../api/cs-workflow';
import { listCsAssignees } from '../api/platform';
import { KvModal } from '../ls-shell/kv';

type Target = { id: string; label: string };
type Outcome = { label: string; result: '바꿈' | '건너뜀' | '실패' | '결과 모름'; note: string };

/**
 * 선택한 문의의 담당을 한 사람으로 바꾼다(CS_K_List_Batch 담당 변경, C/S 관리자만).
 * 문의마다 최신 업무 판본을 읽어 PUT owner를 한 건씩 보낸다. 한 건이 실패해도 나머지는 계속하고 결과를 건별로 보인다.
 * 결과를 모르는 건(408·5xx·통신 오류)은 다시 보내지 않는다. 이미 그 담당이면 보내지 않고 건너뛴다.
 */
export default function BatchOwnerDialog({ targets, onClose, onDone }: { targets: Target[]; onClose: () => void; onDone: () => void }) {
  const mode = getApiMode();
  const assignees = useQuery({ queryKey: ['cs-assignees', mode], queryFn: ({ signal }) => listCsAssignees(signal), retry: false, staleTime: 0 });
  const [owner, setOwner] = useState('');
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<Outcome[] | null>(null);
  const running = useRef(false);

  async function run() {
    if (running.current || !owner) return;
    running.current = true; setBusy(true);
    const done: Outcome[] = [];
    for (const target of targets) {
      const signal = AbortSignal.timeout(30_000);
      try {
        const workflow = await getTicketWorkflow(target.id, signal);
        if (workflow.data.owner_user_id === owner) { done.push({ label: target.label, result: '건너뜀', note: '이미 그 담당입니다' }); continue; }
        await reassignOwner(target.id, { owner_user_id: owner, version: workflow.data.version }, signal);
        done.push({ label: target.label, result: '바꿈', note: '' });
      } catch (error) {
        const status = error instanceof ApiError ? error.status : 0;
        done.push(isVersionConflict(error) ? { label: target.label, result: '실패', note: '그 사이 상태가 바뀌었습니다. 문의 처리 화면에서 다시 하세요' }
          : isInvalidOwner(error) ? { label: target.label, result: '실패', note: '활성 C/S 담당자가 아닙니다' }
            : status >= 400 && status < 500 && status !== 408 ? { label: target.label, result: '실패', note: (error as ApiError).message }
              : { label: target.label, result: '결과 모름', note: '다시 보내지 않았습니다. 문의 처리 화면에서 담당을 확인하세요' });
      }
      setOutcomes([...done]);
    }
    running.current = false; setBusy(false);
    onDone();
  }

  const counts = outcomes ? (['바꿈', '건너뜀', '실패', '결과 모름'] as const).map(kind => `${kind} ${outcomes.filter(item => item.result === kind).length}`).join(', ') : '';
  return <KvModal title={`담당 변경, ${targets.length}건`} onClose={() => !busy && onClose()}>
    <div className="kv-sub" style={{ margin: '0 0 8px' }}>선택한 문의 {targets.length}건의 담당을 한 사람으로 바꿉니다. 상태는 바뀌지 않습니다. C/S 관리자만 할 수 있고 서버가 확인합니다.</div>
    <div className="kv-field"><span className="req">새 담당</span>
      {assignees.isPending ? <span className="ro">담당자 목록을 읽는 중입니다</span> : assignees.isError ? <span className="ro">담당자 목록을 불러오지 못했습니다</span>
        : <select aria-label="새 담당" value={owner} disabled={busy || !!outcomes} onChange={event => setOwner(event.target.value)}>
          <option value="" disabled>선택</option>{assignees.data.data.items.map(item => <option key={item.user_id} value={item.user_id}>{item.display_name}</option>)}</select>}</div>
    {outcomes && <>
      <div className="kv-sub" role="status" style={{ margin: '8px 0 4px' }}>{busy ? `처리 중 ${outcomes.length}/${targets.length}` : `끝남: ${counts}`}</div>
      <ul aria-label="담당 변경 결과" style={{ margin: 0, padding: '0 0 0 16px' }}>{outcomes.map(item => <li key={item.label}>{item.label} · {item.result}{item.note && ` · ${item.note}`}</li>)}</ul>
    </>}
    <div className="kv-actions">
      <button type="button" className="kb" disabled={busy} onClick={onClose}>{outcomes ? '닫기' : '취소'}</button>
      {!outcomes && <button type="button" className="kb primary" disabled={busy || !owner || !assignees.isSuccess} onClick={() => void run()}>변경</button>}
    </div>
  </KvModal>;
}
