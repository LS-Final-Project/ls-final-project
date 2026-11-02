import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode } from '../api/client';
import { getTicketWorkflow, isInvalidOwner, isVersionConflict, reassignOwner } from '../api/cs-workflow';
import { listCsAssignees } from '../api/platform';
import { KvModal } from '../ls-shell/kv';

type Target = { id: string; label: string };
type Outcome = { id: string; label: string; result: '바꿈' | '건너뜀' | '실패' | '결과 모름'; note: string };

/**
 * 선택한 문의의 담당을 한 사람으로 바꾼다(CS_K_List_Batch 담당 변경, C/S 관리자만).
 * 문의마다 최신 업무 판본을 읽어 PUT owner를 한 건씩 보낸다. 한 건이 실패해도 나머지는 계속하고 결과를 건별로 보인다.
 * 결과를 모르는 건(변경 요청을 보낸 뒤의 408·5xx·통신 오류)은 다시 보내지 않는다. 이미 그 담당이거나 종결된 문의는 보내지 않고 건너뛴다
 * (문의 처리 화면도 종결 문의의 담당 변경을 막는다). 대화상자가 사라지면 남은 건은 보내지 않는다.
 */
export default function BatchOwnerDialog({ targets, onClose, onDone }: { targets: Target[]; onClose: () => void; onDone: () => void }) {
  const mode = getApiMode();
  const assignees = useQuery({ queryKey: ['cs-assignees', mode], queryFn: ({ signal }) => listCsAssignees(signal), retry: false, staleTime: 0 });
  const [owner, setOwner] = useState('');
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<Outcome[] | null>(null);
  const running = useRef(false);
  const alive = useRef(true);
  // StrictMode 개발 모드는 effect를 setup→cleanup→setup으로 두 번 돌리므로 setup에서 다시 true로 둔다.
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  async function run() {
    if (running.current || !owner) return;
    running.current = true; setBusy(true);
    const done: Outcome[] = [];
    for (const target of targets) {
      if (!alive.current) return;
      const signal = AbortSignal.timeout(30_000);
      const base = { id: target.id, label: target.label };
      let sent = false;
      try {
        const workflow = await getTicketWorkflow(target.id, signal);
        if (workflow.data.state === 'CLOSED') { done.push({ ...base, result: '건너뜀', note: '종결된 문의입니다' }); continue; }
        if (workflow.data.owner_user_id === owner) { done.push({ ...base, result: '건너뜀', note: '이미 그 담당입니다' }); continue; }
        if (!alive.current) return;
        sent = true;
        await reassignOwner(target.id, { owner_user_id: owner, version: workflow.data.version }, signal);
        done.push({ ...base, result: '바꿈', note: '' });
      } catch (error) {
        const status = error instanceof ApiError ? error.status : 0;
        done.push(isVersionConflict(error) ? { ...base, result: '실패', note: '그 사이 상태가 바뀌었습니다. 문의 처리 화면에서 다시 하세요' }
          : isInvalidOwner(error) ? { ...base, result: '실패', note: '활성 C/S 담당자가 아닙니다' }
            : status >= 400 && status < 500 && status !== 408 ? { ...base, result: '실패', note: (error as ApiError).message }
              : !sent ? { ...base, result: '실패', note: '문의 상태를 읽지 못해 보내지 않았습니다' }
                : { ...base, result: '결과 모름', note: '다시 보내지 않았습니다. 문의 처리 화면에서 담당을 확인하세요' });
      } finally {
        if (alive.current) setOutcomes([...done]);
      }
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
      <ul aria-label="담당 변경 결과" style={{ margin: 0, padding: '0 0 0 16px' }}>{outcomes.map(item => <li key={item.id}>{item.label} · {item.result}{item.note && ` · ${item.note}`}</li>)}</ul>
    </>}
    <div className="kv-actions">
      <button type="button" className="kb" disabled={busy} onClick={onClose}>{outcomes ? '닫기' : '취소'}</button>
      {!outcomes && <button type="button" className="kb primary" disabled={busy || !owner || !assignees.isSuccess} onClick={() => void run()}>변경</button>}
    </div>
  </KvModal>;
}
