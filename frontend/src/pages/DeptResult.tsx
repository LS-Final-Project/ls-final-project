import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { getTicket } from '../api/cs';
import { getTicketWorkflow, isVersionConflict, returnTransfer, submitDeptResult, type WorkflowRecord } from '../api/cs-workflow';
import { me } from '../api/platform';
import { ErrorState, Loading, Preparing } from '../components/states';
import { KvBox, KvFormCell, KvFormRow, KvGrid, KvKey, KvNext, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { DEPT_LABEL, formatStamp } from '../ls-shell/cs-labels';

type Transfer = WorkflowRecord['transfers'][number];
type Result = WorkflowRecord['dept_results'][number];
type Notice = { kind: 'ok' | 'error'; text: string } | null;
const short = (text: string, max = 60) => text.length > max ? `${text.slice(0, max)}…` : text;

// 검토 답변 CS0203(부서 창). 이관받은 부서 담당자가 요청 내용과 고객 원문을 보고 답변을 보낸다.
// 임시 저장은 서버 경로가 없어 눌러지지 않게 둔다. 담당 아님 반송은 사유(필수)와 읽은 판본으로 보낸다.
export default function DeptResult() {
  if (!import.meta.env.DEV) return <Preparing title="검토 답변" message="운영 인증 중계와 권한 검증 후 제공됩니다." />;
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

  if (ticket.isPending || workflow.isPending || identity.isPending) return <main><Loading /></main>;
  const failure = ticket.error ?? workflow.error ?? identity.error;
  if (failure) return <main><ErrorState error={failure} onRetry={() => { void ticket.refetch(); void workflow.refetch(); void identity.refetch(); }} /></main>;
  if (!ticket.data || !workflow.data) return <main><ErrorState error={new ApiError(404, '문의를 찾을 수 없습니다.')} /></main>;
  if (!identity.data) return <main><div className="kv-sub" style={{ margin: '14px' }}>로그인이 필요합니다. <Link to="/account">계정 확인</Link></div></main>;

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

  const columns: KvColumn<Transfer>[] = [
    { key: 'round', header: '차수', width: 44, className: 'c', cell: row => row.round },
    { key: 'dept', header: '부서', width: 130, headClass: '', cell: row => DEPT_LABEL[row.dept_code] ?? row.dept_code },
    { key: 'summary', header: '답변 요약', headClass: '', cell: row => { const r = resultOf(row); return r ? short(r.result) : <span className="unc">{row.status === 'RETURNED_BY_DEPT' ? '반송' : row.status === 'RECALLED' ? '요청 취소' : '답변 없음'}</span>; } },
    { key: 'rework', header: '다시 요청 사유', headClass: '', cell: row => resultOf(row)?.review_note ?? '' },
  ];

  return <main>
    <KvTitle path="부서 검토" title="검토 답변" code="CS0203">
      <button className="kb primary" disabled={!answerable || busy} onClick={() => form.current?.requestSubmit()}>답변 보내기<KvKey>F10</KvKey></button>
      <button className="kb" disabled={!answerable || busy} onClick={() => returnForm.current?.requestSubmit()}>담당 아님 반송</button>
      <Link className="kb" to="/dept/inbox">목록<KvKey>Esc</KvKey></Link>
    </KvTitle>
    {mode === 'mock' && <div className="kv-sub" role="status" style={{ margin: '6px 14px 0' }}>모의 응답 모드 — 계약 예시이며 실제 업무 기록이 아닙니다.</div>}
    {notice && <div className="kv-sub" role={notice.kind === 'error' ? 'alert' : 'status'} style={{ margin: '6px 14px 0' }}>{notice.text}</div>}
    <KvNext>{answerable ? <>다음 <b>답변 보내기</b>. 문의 부서 검토 요청, {current!.round}차</> : current ? <>이 요청은 지금 <b>답변할 수 없습니다</b>. {isDeptStaff ? '이미 답변했거나 요청이 끝났습니다' : '부서 담당자 계정만 답변할 수 있습니다'}</> : <>이 부서로 이관된 요청이 없습니다</>}</KvNext>
    <div className="kv-form">
      <KvFormRow columns={4}>
        <KvFormCell label="문의번호">{t.external_id ?? t.id.slice(0, 8)}</KvFormCell>
        <KvFormCell label="접수일시">{formatStamp(t.received_at)}</KvFormCell>
        <KvFormCell label="고객">{t.customer_external_id}</KvFormCell>
        <KvFormCell label="제품">{t.product_code}</KvFormCell>
      </KvFormRow>
      <KvFormRow columns={4}>
        <KvFormCell label="요청 차수">{current ? `${current.round}차` : '-'}</KvFormCell>
        <KvFormCell label="요청일시">{current ? formatStamp(current.transferred_at) : '-'}</KvFormCell>
        <KvFormCell label="요청자">{current ? current.transferred_by.slice(0, 8) : '-'}</KvFormCell>
        <KvFormCell label="펌웨어">{t.fw_version_reported ?? '미확인'}</KvFormCell>
      </KvFormRow>
    </div>
    {current && <div className="kv-line full"><b>요청 사유</b>{current.reason}</div>}
    <div className="kv-split">
      <KvBox title="고객 원문" note={`수신 ${formatStamp(t.received_at)}`}>
        <div className="kv-text" lang={t.body_language} style={{ minHeight: 150 }}>{t.body}</div>
        {previous.length > 0 && <>
          <div className="kv-sub" style={{ margin: '10px 0 6px' }}>이전 차수</div>
          <KvGrid label="이전 차수" columns={columns} rows={previous} rowKey={row => row.id} fallback="" />
        </>}
      </KvBox>
      <KvBox title="답변 입력">
        <form ref={form} aria-label="검토 답변 입력" onSubmit={event => { event.preventDefault(); send(new FormData(event.currentTarget)); }}>
          <div className="kv-field"><span className="req">답변</span><textarea name="result" aria-label="답변" rows={6} required maxLength={10000} disabled={!answerable || busy} /></div>
          <div className="kv-field"><span>고객 회신 문안</span><textarea name="reply" aria-label="고객 회신 문안" rows={4} maxLength={10000} disabled={!answerable || busy} /></div>
          <div className="kv-actions">
            <button type="button" className="kb" disabled title="임시 저장 경로가 서버에 아직 없습니다">임시 저장</button>
            <button type="submit" className="kb primary" disabled={!answerable || busy}>{busy ? '보내는 중' : '답변 보내기'}</button>
          </div>
        </form>
        <div className="kv-sub" style={{ margin: '12px 0 4px', borderTop: '1px solid var(--erp-border-soft)', paddingTop: 10 }}>담당 아님 반송</div>
        <form ref={returnForm} aria-label="담당 아님 반송 입력" onSubmit={event => { event.preventDefault(); giveBack(new FormData(event.currentTarget)); }}>
          <div className="kv-field"><span className="req">반송 사유</span><textarea name="reason" aria-label="반송 사유" rows={2} required maxLength={2000} disabled={!answerable || busy} /></div>
          <div className="kv-actions"><button type="submit" className="kb" disabled={!answerable || busy}>담당 아님 반송</button></div>
        </form>
      </KvBox>
    </div>
  </main>;
}
