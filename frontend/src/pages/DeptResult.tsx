import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getTicket } from '../api/cs';
import { getTicketWorkflow, INVALID_TRANSITION_TEXT, isInvalidTransition, isVersionConflict, returnTransfer, submitDeptResult, type WorkflowRecord } from '../api/cs-workflow';
import { me } from '../api/platform';
import { ErrorState, Loading } from '../components/states';
import TicketSourcePane from '../components/TicketSourcePane';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { useHotkey } from '../ls-shell/kv';
import LogTable, { type LogColumn } from './LogTable';
import './ticket-detail-v3.css';
import './dept-result-v3.css';
import { DEPT_LABEL, formatStamp } from '../ls-shell/cs-labels';

type Transfer = WorkflowRecord['transfers'][number];
type Result = WorkflowRecord['dept_results'][number];
type Notice = { kind: 'ok' | 'error'; text: string } | null;
const short = (text: string, max = 60) => text.length > max ? `${text.slice(0, max)}…` : text;

// 검토 답변 CS0203(부서 창). 이관받은 부서 담당자가 요청 내용과 고객 원문을 보고 답변을 보낸다.
// 임시 저장은 서버 경로가 없어 눌러지지 않게 둔다. 담당 아님 반송은 사유(필수)와 읽은 판본으로 보낸다.
export default function DeptResult() {
  const access = useBusinessAccess('cs');
  if (!access.ready) return <BusinessGate access={access} title="검토 답변" />;
  return <ResultView />;
}

function ResultView() {
  const { ticketId = '' } = useParams();
  const mode = getApiMode();
  const navigate = useNavigate();
  const identity = useQuery({ queryKey: ['dept-result-identity', mode], queryFn: me, retry: false });
  const ticket = useQuery({ queryKey: ['dept-result-ticket', mode, ticketId], queryFn: ({ signal }) => getTicket(ticketId, signal), retry: false, staleTime: 0 });
  const workflow = useQuery({ queryKey: ['dept-result-workflow', mode, ticketId], queryFn: ({ signal }) => getTicketWorkflow(ticketId, signal), retry: false, staleTime: 0 });
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  const returnForm = useRef<HTMLFormElement>(null);
  useHotkey('F10', () => form.current?.requestSubmit(), !busy);
  useHotkey('Escape', () => navigate('/dept/inbox'));

  if (ticket.isPending || workflow.isPending || identity.isPending) return <main className="td3 dr3"><div className="body"><Loading /></div></main>;
  const failure = ticket.error ?? workflow.error ?? identity.error;
  if (failure) return <main className="td3 dr3"><div className="body"><ErrorState error={failure} onRetry={() => { void ticket.refetch(); void workflow.refetch(); void identity.refetch(); }} /></div></main>;
  if (!ticket.data || !workflow.data) return <main className="td3 dr3"><div className="body"><ErrorState error={new ApiError(404, '문의를 찾을 수 없습니다.')} /></div></main>;
  if (!identity.data) return <main className="td3 dr3"><div className="body"><div className="stale">로그인이 필요합니다. <Link to="/account">계정 확인</Link></div></div></main>;

  const t = ticket.data.data;
  const wf = workflow.data.data;
  const dept = identity.data.data.dept;
  const isDeptStaff = identity.data.data.roles.includes('dept_staff');
  const mine = wf.transfers.filter(item => !dept || item.dept_code === dept);
  const current: Transfer | undefined = [...mine].sort((a, b) => b.round - a.round)[0];
  const answerable = isDeptStaff && current?.status === 'OPEN';
  const previous = mine.filter(item => item.id !== current?.id).sort((a, b) => a.round - b.round);
  const resultOf = (transfer: Transfer): Result | undefined => wf.dept_results.find(item => item.transfer_id === transfer.id);

  async function act(done: string, call: (signal: AbortSignal) => Promise<unknown>) {
    if (busy || !answerable) return;
    setBusy(true); setNotice(null);
    const controller = new AbortController();
    try {
      await call(controller.signal);
      setNotice({ kind: 'ok', text: done });
    } catch (error) {
      setNotice({ kind: 'error', text: isVersionConflict(error) ? '다른 사용자가 먼저 상태를 바꿨습니다. 최신 상태를 다시 읽었으니 확인한 뒤 다시 보내세요.'
        : isInvalidTransition(error) ? INVALID_TRANSITION_TEXT
        : error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 ? error.message
          : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 상태를 읽었습니다. 반영 여부를 확인하세요.' });
    } finally {
      setBusy(false);
      await Promise.allSettled([workflow.refetch(), ticket.refetch()]);
    }
  }
  const send = (data: FormData) => {
    const result = String(data.get('result') ?? '').trim();
    const reply = String(data.get('reply') ?? '').trim();
    if (!result) return;
    void act('답변을 보냈습니다. C/S 담당자가 확인합니다.', signal => submitDeptResult(ticketId, { version: wf.version, result, reply_content: reply || null }, signal));
  };
  const giveBack = (data: FormData) => {
    const reason = String(data.get('reason') ?? '').trim();
    if (!reason) return;
    void act('담당 아님으로 반송했습니다. C/S 담당자가 다시 검토합니다.', signal => returnTransfer(ticketId, { version: wf.version, reason }, signal));
  };

  const columns: LogColumn<Transfer>[] = [
    { key: 'round', header: '차수', width: 56, className: 'c', cell: row => `${row.round}차` },
    { key: 'dept', header: '부서', width: 130, cell: row => DEPT_LABEL[row.dept_code] ?? row.dept_code },
    { key: 'summary', header: '답변 요약', cell: row => { const r = resultOf(row); return r ? short(r.result) : <span className="none">{row.status === 'RETURNED_BY_DEPT' ? '반송' : row.status === 'RECALLED' ? '요청 취소' : '답변 없음'}</span>; } },
    { key: 'rework', header: '다시 요청 사유', cell: row => resultOf(row)?.review_note ?? '' },
  ];
  const code = t.external_id ?? t.id.slice(0, 8);
  // 지금 할 일 줄은 두지 않는다(사용자 결정). 답할 수 없는 이유는 답변 입력 상자에 적는다.
  const lockedText = answerable ? null : current ? (isDeptStaff ? '이미 답변했거나 요청이 끝났습니다.' : '부서 담당자 계정만 답변할 수 있습니다.') : '이 부서로 이관된 요청이 없습니다.';

  return <main className="td3 dr3">
    <div className="title">
      <span className="path">부서 검토 ›</span><h1>검토 답변</h1><code className="kv-code">CS0203</code>
      <div className="btns">
        <button className="kb3" disabled={!answerable || busy} onClick={() => returnForm.current?.requestSubmit()}>담당 아님 반송</button>
        <span className="vsep" />
        <button className="kb3 pri" disabled={!answerable || busy} onClick={() => form.current?.requestSubmit()}>답변 보내기 <kbd>F10</kbd></button>
        <Link className="kb3" to="/dept/inbox">목록 <kbd>Esc</kbd></Link>
      </div>
    </div>

    <div className="body">
      {mode === 'mock' && <div className="tier-note" role="status">모의 응답 모드 — 계약 예시이며 실제 업무 기록이 아닙니다.</div>}
      {notice && <div className={notice.kind === 'error' ? 'note err' : 'note'} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</div>}
      <div className="info">
        <div className="k">문의번호</div><div className="v">{code}</div>
        <div className="k">접수일시</div><div className="v">{formatStamp(t.received_at)}</div>
        <div className="k">고객</div><div className="v">{t.customer_external_id}</div>
        <div className="k">제품</div><div className="v last">{t.product_code}</div>
        <div className="k nb">요청 차수</div><div className="v nb">{current ? `${current.round}차` : '-'}</div>
        <div className="k nb">요청일시</div><div className="v nb">{current ? formatStamp(current.transferred_at) : '-'}</div>
        <div className="k nb">요청자</div><div className="v nb">{current ? current.transferred_by.slice(0, 8) : '-'}</div>
        <div className="k nb">펌웨어</div><div className="v nb last">{t.fw_version_reported ?? '미확인'}</div>
      </div>

      {current && <div className="dr-reason"><small>C/S 요청 사유</small><p>{current.reason}</p></div>}

      <div className="dr-cols">
        <div className="pane">
          <TicketSourcePane ticket={t} canCreate={isDeptStaff && !!current} onNote={(kind, text) => setNotice({ kind, text })} />
          {previous.length > 0 && <div className="pb dr-prev">
            <h3>이전 차수</h3>
            <LogTable label="이전 차수" columns={columns} rows={previous} rowKey={row => row.id} fallback="" />
          </div>}
        </div>
        <div className="pane">
          <div className="ph">답변 입력</div>
          <div className="pb">
            {lockedText && <p className="dr-locked" role="note">{lockedText}</p>}
            <form ref={form} className="dr-form" aria-label="검토 답변 입력" onSubmit={event => { event.preventDefault(); send(new FormData(event.currentTarget)); }}>
              <label><span className="req">답변</span><textarea name="result" aria-label="답변" rows={7} required maxLength={10000} disabled={!answerable || busy} placeholder="원인, 판단, 필요한 조치를 적습니다. C/S 담당자가 확인합니다." /></label>
              <label><span>고객 회신 문안 <small>선택</small></span><textarea name="reply" aria-label="고객 회신 문안" rows={4} maxLength={10000} disabled={!answerable || busy} placeholder="고객에게 그대로 전할 문장이 있으면 적습니다." /></label>
              <div className="dr-acts">
                <button type="button" className="kb3" disabled title="임시 저장 경로가 서버에 아직 없습니다">임시 저장</button>
                <button type="submit" className="kb3 pri" disabled={!answerable || busy}>{busy ? '보내는 중' : '답변 보내기'}</button>
              </div>
            </form>
            <form ref={returnForm} className="dr-form back" aria-label="담당 아님 반송 입력" onSubmit={event => { event.preventDefault(); giveBack(new FormData(event.currentTarget)); }}>
              <h3>담당 아님 반송</h3>
              <label><span className="req">반송 사유</span><textarea name="reason" aria-label="반송 사유" rows={2} required maxLength={2000} disabled={!answerable || busy} placeholder="우리 부서 일이 아닌 이유와 맞는 부서를 적습니다." /></label>
              <div className="dr-acts"><button type="submit" className="kb3" disabled={!answerable || busy}>담당 아님 반송</button></div>
            </form>
          </div>
        </div>
      </div>
    </div>
  </main>;
}
