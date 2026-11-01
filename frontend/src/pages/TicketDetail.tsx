import { useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import TicketWorkflow from '../components/TicketWorkflow';
import TicketUnits from '../components/TicketUnits';
import TicketAnalysisPanel from '../components/TicketAnalysisPanel';
import ResolutionCaseForm from '../components/ResolutionCaseForm';
import DeptResultReview from '../components/DeptResultReview';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ApiError, getApiMode } from '../api/client';
import { getTicket, getTicketAnalysis } from '../api/cs';
import { closeTicket, confirmReturn, correctAnalysis, getTicketWorkflow, isInvalidOwner, isSendPending, isResolutionExists, isResultVersionConflict, isVersionConflict, reassignOwner, recallTransfer, registerResolutionCase, reviewDeptResult, startReview, transferTicket, type DeptCode, type WorkflowRecord, refreshCustomerSend, retryCustomerSend, sendCustomerMessage } from '../api/cs-workflow';
import { listCsAssignees, me } from '../api/platform';
import { listMailboxes } from '../api/mailbox';
import { ErrorState, Loading } from '../components/states';
import CustomerThread, { isUnsettledSend, latestUnansweredReply } from '../components/CustomerThread';
import TicketAudit from '../components/TicketAudit';
import TicketSummary from '../components/TicketSummary';
import AiDraftDialog, { type SendOptions } from '../components/AiDraftDialog';
import AnalysisCorrection from '../components/AnalysisCorrection';
import { KvGrid, KvKey, KvModal, KvMore, KvTitle, useHotkey, type KvColumn } from '../ls-shell/kv';
import { CHANNEL_LABEL, STATE_LABEL, formatStamp } from '../ls-shell/cs-labels';

const PENDING = '연결 전입니다.';
const DEPT_LABEL: Record<DeptCode, string> = {
  TS_LV: '저압기기 기술지원', TS_MV: '중압기기 기술지원', TS_TRF: '변압기 기술지원', TS_AUTO: '자동화 기술지원',
  TS_ENERGY: '에너지 기술지원', RND_FW: '펌웨어 개발', QA_CLAIM: '품질보증/클레임', FIELD_SVC: '현장 서비스',
  CERT: '인증', SALES_OPS: '영업 운영', SCM: '주문/물류', WARRANTY_DESK: '보증 데스크',
};
const TRANSFER_STATUS: Record<WorkflowRecord['transfers'][number]['status'], string> = {
  OPEN: '검토 중', RESULT_SUBMITTED: '결과 제출', RETURNED_BY_DEPT: '부서 반송', RECALLED: 'C/S 회수',
};
type Transfer = WorkflowRecord['transfers'][number];
type Dialog = 'transfer' | 'recall' | 'close' | 'resolution' | 'owner' | 'draft' | 'correct' | null;
type Notice = { kind: 'ok' | 'error'; text: string } | null;

const Cell = ({ label, children }: { label: string; children: ReactNode }) =>
  <label><span>{label}</span><div className="v">{children}</div></label>;
const Row = ({ children }: { children: ReactNode }) =>
  <div className="row" style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>{children}</div>;

// 문의 처리 CS0102. 접수 원문·업무 상태·장비 후보 조회와 업무 흐름 4동작(검토 시작·부서 검토 요청·요청 취소·종결)을 연결한다.
// AI 분류·요약, 처리 기록은 API가 없어 연결 전으로 표시한다.
export default function TicketDetail() {
  const { ticketId = '' } = useParams();
  const navigate = useNavigate();
  const mode = getApiMode();
  const access = useBusinessAccess('cs');
  const ticket = useQuery({ queryKey: ['tickets', mode, ticketId], queryFn: ({ signal }) => getTicket(ticketId, signal), enabled: access.ready && ticketId !== '' });
  // 기한을 읽는 사람이 등급이 추정임을 알아야 한다. 안내일 뿐이라 분석을 못 읽어도 화면은 그대로다.
  const analysis = useQuery({ queryKey: ['ticket-analysis', mode, ticketId], queryFn: ({ signal }) => getTicketAnalysis(ticketId, signal), enabled: access.ready && ticketId !== '', retry: false });
  const tierNote = analysis.data?.data?.rules?.tier_note;
  // 분석 응답이 계약 모양(필드 표)이 아니면 쓰지 않는다. 모의 모드의 빈 응답도 여기서 걸러진다.
  const analysisData = analysis.isSuccess && analysis.data.data && typeof analysis.data.data.fields === 'object' && analysis.data.data.fields ? analysis.data.data : undefined;
  // 상태 표시는 문의 상세의 값을 쓴다. 업무 흐름 조회는 판본(version), 담당, 검토 요청 이력만 읽는다.
  const workflow = useQuery({ queryKey: ['workflow', mode, ticketId], queryFn: ({ signal }) => getTicketWorkflow(ticketId, signal), enabled: access.ready && ticketId !== '' && ticket.isSuccess });
  // 업무 접근 확인(['me'])과 캐시를 섞지 않으려고 키를 따로 둔다. 담당자 이름 표시에만 쓴다.
  const identity = useQuery({ queryKey: ['ticket-owner-identity', mode], queryFn: me, retry: false, enabled: access.ready && workflow.isSuccess });
  const [dialog, setDialog] = useState<Dialog>(null);
  const assignees = useQuery({ queryKey: ['cs-assignees', mode], queryFn: ({ signal }) => listCsAssignees(signal), enabled: dialog === 'owner', retry: false, staleTime: 0 });
  const mailboxes = useQuery({ queryKey: ['send-mailboxes', mode], queryFn: () => listMailboxes(), enabled: dialog === 'draft' && import.meta.env.DEV && mode === 'live', retry: false, staleTime: 0 });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  // 반품 확정 입력 칸을 비우는 데 쓴다. 확정은 모든 칸을 덮어쓰는 기록이라 입력이 다른 문의나 다음 확정에 남으면 안 된다.
  const [returnFormKey, setReturnFormKey] = useState(0);
  const running = useRef<AbortController | null>(null);
  useHotkey('Escape', () => navigate('/cs/tickets'), access.ready && dialog === null);

  if (!access.ready) return <BusinessGate access={access} title="문의 처리" />;
  const data = ticket.isSuccess ? ticket.data.data : undefined;
  const rc = data?.resolution_case;
  // 응답이 계약 모양(판본 숫자, 이력 배열)이 아니면 쓰지 않는다.
  const wf = workflow.isSuccess && typeof workflow.data.data?.version === 'number' && Array.isArray(workflow.data.data.transfers) ? workflow.data.data : undefined;
  const replyArrived = latestUnansweredReply(wf);
  const state = data ? STATE_LABEL[data.workflow_state] ?? '알 수 없는 상태, 확인 필요' : '-';
  // 부서가 답했지만 아직 확인하지 않은 가장 최근 답변. 확인하기 전에는 부서 검토 요청과 종결을 막는다.
  const pendingResult = (wf?.dept_results ?? []).filter(item => item.review_status === 'SUBMITTED').at(-1);
  const roundOf = (transferId: string) => wf?.transfers.find(item => item.id === transferId)?.round ?? null;
  const REVIEW_LABEL = { SUBMITTED: '확인 전', ACCEPTED: '확인 완료', REWORK_REQUESTED: '다시 요청' } as const;
  const reviewOf = (transferId: string) => { const found = (wf?.dept_results ?? []).filter(item => item.transfer_id === transferId).at(-1); return found ? REVIEW_LABEL[found.review_status] ?? found.review_status : '-'; };
  const canWrite = import.meta.env.DEV && mode === 'live';
  const why = (ok: boolean, note: string) => canWrite ? (wf ? (ok ? undefined : note) : '업무 판본을 읽는 중입니다') : '실제 API 개발 모드에서만 실행할 수 있습니다';
  const st = data?.workflow_state;
  // 담당 변경은 C/S 관리자만 할 수 있다(서버도 확인한다).
  const isManager = identity.data?.data?.roles?.includes('manager') === true;
  const reasons = {
    start: why(st === 'RECEIVED', '이미 검토중'),
    transfer: why(st === 'IN_REVIEW' && !pendingResult, pendingResult ? '확인 전 답변이 있으면 요청할 수 없습니다' : '검토중인 문의만 요청할 수 있습니다'),
    recall: why(st === 'TRANSFERRED', '진행 중인 요청 없음'),
    close: why((st === 'ANSWERED' || st === 'IN_REVIEW') && !pendingResult, pendingResult ? '확인 전 답변이 있으면 불가' : '종결할 수 없는 상태입니다'),
    owner: !isManager ? 'C/S 관리자만' : why(st !== 'CLOSED', '종결된 문의'),
    resolution: why(st === 'CLOSED' && !rc, rc ? '이미 해결 기록이 있습니다(문의당 한 건)' : '종결 후 한 건'),
  };
  const owner = wf?.owner_user_id ? (identity.data?.data?.id === wf.owner_user_id ? '나' : `사용자 ${wf.owner_user_id.slice(0, 8)}`) : wf ? '미지정' : '-';

  // 한 번에 한 동작만 실행한다. 실패하거나 결과를 알 수 없으면(408·5xx) 같은 요청을 다시 보내지 않고 최신 상태를 읽는다.
  // 판본이 바뀌면 서버가 409를 주므로 이미 반영된 동작이 두 번 적용되지 않는다.
  async function run(done: string | ((result: unknown) => NonNullable<Notice>), call: (signal: AbortSignal) => Promise<unknown>): Promise<boolean> {
    if (busy) return false;
    const controller = new AbortController();
    running.current = controller;
    setBusy(true); setNotice(null);
    try {
      const result = await call(controller.signal);
      setNotice(typeof done === 'function' ? done(result) : { kind: 'ok', text: done });
      setDialog(null);
      return true;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return false;
      setNotice({ kind: 'error', text: isVersionConflict(error)
        ? '다른 사용자가 먼저 상태를 바꿨습니다. 최신 상태를 다시 읽었으니 확인한 뒤 다시 실행하세요.'
        : isSendPending(error) ? '결과가 정해지지 않은 발송이 있습니다. 대화에서 "상태 확인"이나 "다시 요청"으로 먼저 확인하세요.'
        : isInvalidOwner(error) ? '새 담당자로 지정할 수 없는 사용자입니다. 활성 C/S 담당자만 지정할 수 있습니다.'
        : isResultVersionConflict(error) ? '부서 답변이 그 사이 바뀌었습니다. 최신 답변을 다시 읽었으니 확인한 뒤 다시 저장하세요.'
        : isResolutionExists(error) ? '이 문의에는 이미 해결 기록이 있습니다. 문의당 한 건만 등록할 수 있고 고치는 경로는 없습니다.'
        : error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 ? error.message
          : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 상태를 읽었습니다. 반영 여부를 확인하세요.' });
      setDialog(null);
      return false;
    } finally {
      await Promise.allSettled([ticket.refetch(), workflow.refetch()]);
      setBusy(false);
    }
  }
  const version = wf?.version ?? 0;
  // 승인했지만 결과가 정해지지 않은 발송. 있으면 새 발송을 막고(서버도 409), 대화에서 먼저 상태를 확인하게 한다.
  const unsettledSend = (wf?.customer_sends ?? []).find(isUnsettledSend);
  const sendBlock = !canWrite ? '실제 API 개발 모드에서만 실행할 수 있습니다' : !wf ? '업무 판본을 읽는 중입니다'
    : st !== 'IN_REVIEW' ? '검토중인 문의만 보낼 수 있습니다' : pendingResult ? '확인 전 부서 답변이 있으면 보낼 수 없습니다'
    : unsettledSend ? '결과가 정해지지 않은 발송이 있습니다. 대화에서 상태를 먼저 확인하세요' : undefined;
  const sendOptions: SendOptions = {
    originalSubject: data?.subject ?? null, defaultTo: wf?.mail_origin?.sender_address ?? null,
    mailboxes: mailboxes.isSuccess ? (mailboxes.data ?? []).filter(item => item.state === 'connected' && !item.reconnect_required).map(item => ({ id: item.mailbox_id, label: `사서함 ${item.mailbox_id.slice(0, 8)}` })) : undefined,
    mailboxesError: mailboxes.isError, defaultMailboxId: wf?.mail_origin?.mailbox_id ?? null, disabled: sendBlock, busy,
    onSend: body => void run(reply => sendNotice(reply), signal => sendCustomerMessage(ticketId, { ...body, version }, signal)),
  };
  // 넘긴 결과는 HTTP 상태가 아니라 마지막 발송의 request_status에 있다. 결과를 모르는 경우에는 같은 내용을 새로 승인하지 않게 안내한다.
  function sendNotice(reply: unknown): NonNullable<Notice> {
    const last = (reply as { data?: { customer_sends?: { request_status?: string; request_error?: string | null }[] } } | null)?.data?.customer_sends?.at(-1);
    switch (last?.request_status) {
      case 'REGISTERED': return { kind: 'ok', text: '승인해 플랫폼에 넘겼습니다. 대화의 "상태 확인"으로 접수 여부를 읽으면 문의 상태가 바뀝니다.' };
      case 'REJECTED': return { kind: 'error', text: `플랫폼이 발송을 거부했습니다(${last.request_error ?? '사유 없음'}). 내용을 고쳐 다시 승인하세요.` };
      case 'REQUEST_FAILED': return { kind: 'error', text: '플랫폼에 연결하지 못해 발송이 만들어지지 않았습니다. 대화의 "다시 요청"으로 같은 내용을 다시 요청하세요.' };
      default: return { kind: 'error', text: '플랫폼의 응답을 확인하지 못했습니다. 같은 내용을 새로 승인하지 말고 대화의 "다시 요청"이나 "상태 확인"으로 결과를 먼저 확인하세요.' };
    }
  }
  const sendActions = canWrite ? { busy, refresh: (id: string) => void run('발송 상태를 읽었습니다.', signal => refreshCustomerSend(ticketId, id, signal)), retry: (id: string) => void run(reply => sendNotice(reply), signal => retryCustomerSend(ticketId, id, version, signal)) } : undefined;
  const doStart = () => run('검토를 시작했습니다.', signal => startReview(ticketId, version, signal));
  const submitTransfer = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const dept = String(form.get('dept') ?? '') as DeptCode;
    const reason = String(form.get('reason') ?? '').trim();
    if (!dept || !reason) return;
    void run('부서 검토를 요청했습니다.', signal => transferTicket(ticketId, { dept_code: dept, reason, version }, signal));
  };
  const submitRecall = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const reason = String(new FormData(event.currentTarget).get('reason') ?? '').trim();
    if (!reason) return;
    void run('부서 검토 요청을 취소했습니다.', signal => recallTransfer(ticketId, { reason, version }, signal));
  };

  const transferColumns: KvColumn<Transfer>[] = [
    { key: 'round', header: '회차', width: 44, className: 'c', cell: item => item.round },
    { key: 'dept', header: '요청 부서', width: 150, cell: item => `${DEPT_LABEL[item.dept_code as DeptCode] ?? item.dept_code} (${item.dept_code})` },
    { key: 'status', header: '상태', width: 80, className: 'c', cell: item => TRANSFER_STATUS[item.status] ?? item.status },
    { key: 'reason', header: '사유', cell: item => item.reason },
    { key: 'at', header: '요청 일시', width: 130, className: 'd', cell: item => formatStamp(item.transferred_at) },
    { key: 'review', header: '답변 확인', width: 68, className: 'c', cell: item => reviewOf(item.id) },
  ];

  return <main>
    <KvTitle path="C/S 관리" title="문의 처리" code="CS0102">
      <button className="kb primary" disabled title="저장할 항목이 아직 없습니다">저장<KvKey>F10</KvKey></button>
      <button className="kb" disabled={busy || reasons.transfer !== undefined} title={reasons.transfer} onClick={() => setDialog('transfer')}>부서 검토 요청</button>
      <Link className="kb" to="/cs/tickets">목록<KvKey>Esc</KvKey></Link>
      <KvMore items={[
        { label: '검토 시작', note: reasons.start, action: busy || reasons.start !== undefined ? undefined : () => void doStart() },
        { label: '요청 취소', note: reasons.recall, action: busy || reasons.recall !== undefined ? undefined : () => setDialog('recall') },
        { label: '분류 정정', note: !analysisData ? 'AI 분류를 읽은 뒤에 정정할 수 있습니다' : !canWrite ? '실제 API 개발 모드에서만 실행할 수 있습니다' : 'AI 분류를 사유와 함께 정정합니다', action: analysisData && canWrite && !busy ? () => setDialog('correct') : undefined },
        { label: 'AI 초안 보기', note: 'AI 초안 3개를 읽고 고르거나 직접 쓰고, 확인해 승인하면 고객에게 보냅니다', action: () => setDialog('draft') },
        { label: '담당 변경', note: reasons.owner, action: busy || reasons.owner !== undefined ? undefined : () => setDialog('owner') },
        { label: '종결', note: reasons.close ?? '확인 완료된 검토 답변 또는 고객 발송 기록 필요', action: busy || reasons.close !== undefined ? undefined : () => setDialog('close') },
        { label: '해결 기록 남기기', note: reasons.resolution ?? '종결 후 한 건', action: busy || reasons.resolution !== undefined ? undefined : () => setDialog('resolution') },
      ]} />
    </KvTitle>

    <div className="kv-next">
      <span className="k">다음 할 일</span>
      {replyArrived && <><span><b>고객 회신 도착</b> {formatStamp(replyArrived.received_at)}{replyArrived.reopened ? ', 검토중으로 복귀' : ''}</span><span className="sep">|</span></>}
      {data ? <TicketWorkflow state={data.workflow_state} /> : <span>{ticket.isPending ? '불러오는 중' : '문의를 불러오지 못했습니다.'}</span>}
      <button className="kb" style={{ marginLeft: 'auto', flex: 'none', whiteSpace: 'nowrap' }} disabled={ticket.isFetching || busy} onClick={() => { void ticket.refetch(); void workflow.refetch(); }}>최신 상태 조회</button>
    </div>
    <div className="kv-sub" style={{ margin: '4px 14px 0' }}>API 조회 상태와 상태별 일반 안내입니다. 실행 권한이나 후속 작업의 완료를 보장하지 않습니다. 요약, 분류, 문안은 아직 없음, KAN-103과 106 뒤</div>
    {notice && <div className={notice.kind === 'error' ? 'kv-err' : 'kv-next'} role={notice.kind === 'error' ? 'alert' : 'status'} style={{ margin: '6px 14px 0', padding: '6px 10px' }}>{notice.text}</div>}

    {tierNote && <div className="kv-sub" role="note" style={{ margin: '4px 14px 0' }}>{tierNote}</div>}

    {ticket.isPending && <Loading />}
    {ticket.isError && <ErrorState error={ticket.error} onRetry={() => void ticket.refetch()} />}
    {data && <>
      <div className="kv-form">
        <Row>
          <Cell label="문의번호"><span className="ro">{data.external_id ?? data.id.slice(0, 8)}</span></Cell>
          <Cell label="접수일시"><span className="ro">{formatStamp(data.received_at)}</span></Cell>
          <Cell label="고객"><span className="ro">{data.customer_external_id}</span></Cell>
          <Cell label="접수 경로"><span className="ro">{CHANNEL_LABEL[data.channel]}, {data.body_language}</span></Cell>
        </Row>
        <Row>
          <Cell label="제품"><span className="ro">{data.product_code}</span></Cell>
          <Cell label="상태"><span className="ro">{state}</span></Cell>
          <Cell label="담당"><span className="ro">{owner}</span></Cell>
          <Cell label="자료 구분"><span className="ro">{data.data_origin}, {data.derivation_kind}</span></Cell>
        </Row>
      </div>

      <div className="kv-split c3">
        <section className="kv-box">
          <h2>대화<small>최신이 위</small></h2>
          <div className="body">
            <TicketSummary ticketId={ticketId} canWrite={canWrite} />
            <CustomerThread original={data} workflow={wf} actions={sendActions} />
          </div>
        </section>
        <section className="kv-box">
          <h2>설치 장비 확인<small>확인 전</small></h2>
          <div className="body"><TicketUnits key={ticketId} ticketId={ticketId} canWrite={canWrite} /></div>
        </section>
        <TicketAnalysisPanel key={`${ticketId}:${returnFormKey}`} analysis={analysisData} pending={analysis.isPending} workflow={wf} ticketState={data.workflow_state} canWrite={canWrite} busy={busy}
          onConfirm={form => void run('반품 사유를 확정했습니다.', signal => confirmReturn(ticketId, { ...form, version }, signal)).then(ok => { if (ok) setReturnFormKey(key => key + 1); })} />
      </div>

      <div className="kv-split">
        <div>
          <div className="kv-sub" style={{ margin: '0 0 6px' }}>검토 요청</div>
          {wf
            ? <KvGrid label="검토 요청 이력" columns={transferColumns} rows={wf.transfers} rowKey={item => item.id} fallback="요청 이력 없음" />
            : <div className="kv-sub" style={{ margin: 0, padding: '6px 10px', border: '1px solid var(--erp-border)', background: '#fff', color: 'var(--erp-muted)' }}>{workflow.isError ? '요청 이력을 불러오지 못했습니다.' : '요청 이력을 읽는 중입니다.'}</div>}
          {pendingResult && <div style={{ marginTop: 8 }}><DeptResultReview key={pendingResult.id} result={pendingResult} round={roundOf(pendingResult.transfer_id)} busy={busy}
            blocked={canWrite ? undefined : '실제 API 개발 모드에서만 실행할 수 있습니다'}
            onSubmit={form => void run(form.decision === 'ACCEPT' ? '부서 답변을 확인했습니다.' : '같은 부서에 다시 요청했습니다.', signal => reviewDeptResult(ticketId, pendingResult.id, { ...form, version }, signal))} /></div>}
          <div className="kv-sub" style={{ margin: '12px 0 0' }}>알려진 해결 사례</div>
          <div className="kv-sub" style={{ margin: 0, padding: '6px 10px', border: '1px solid var(--erp-border)', background: '#fff', color: 'var(--erp-text)' }}>
            {rc
              ? <>사례 {rc.external_id ?? '-'}. 증상 {rc.symptom}. 원인 {rc.root_cause} ({rc.root_cause_type}). 조치 {rc.action_taken}. 처리 부서 {rc.resolved_by_dept}. 현장 방문 {rc.onsite_visit ? '있음' : '없음'}. 재발 방지 {rc.recurrence_prevention ?? '-'}</>
              : '연결된 해결 사례가 없습니다.'}
          </div>
        </div>
        <div>
          <div className="kv-sub" style={{ margin: '0 0 6px' }}>처리 기록</div>
          <TicketAudit ticketId={ticketId} />
        </div>
      </div>
    </>}

    {dialog === 'transfer' && data && <KvModal title={`부서 검토 요청, ${data.external_id ?? data.id.slice(0, 8)}`} onClose={() => !busy && setDialog(null)}>
      <form onSubmit={submitTransfer}>
        <div className="kv-field"><span>현재</span><span className="ro">{state}, 담당 {owner}</span></div>
        <div className="kv-field"><span className="req">요청 부서</span>
          <select name="dept" aria-label="요청 부서" required defaultValue=""><option value="" disabled>선택</option>{(Object.keys(DEPT_LABEL) as DeptCode[]).map(code => <option key={code} value={code}>{DEPT_LABEL[code]} ({code})</option>)}</select></div>
        <div className="kv-field"><span className="req">요청 사유</span><textarea name="reason" aria-label="요청 사유" rows={3} required /></div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(null)}>취소</button><button type="submit" className="kb primary" disabled={busy}>요청</button></div>
      </form>
    </KvModal>}
    {dialog === 'recall' && data && <KvModal title={`요청 취소, ${data.external_id ?? data.id.slice(0, 8)}`} onClose={() => !busy && setDialog(null)}>
      <form onSubmit={submitRecall}>
        <div className="kv-sub" style={{ margin: '0 0 8px' }}>부서에 보낸 검토 요청을 회수하고 검토중 상태로 돌아갑니다.</div>
        <div className="kv-field"><span className="req">취소 사유</span><textarea name="reason" aria-label="취소 사유" rows={3} required /></div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(null)}>닫기</button><button type="submit" className="kb primary" disabled={busy}>요청 취소</button></div>
      </form>
    </KvModal>}
    {dialog === 'resolution' && data && <KvModal title={`해결 기록 남기기, ${data.external_id ?? data.id.slice(0, 8)}`} onClose={() => !busy && setDialog(null)}>
      <ResolutionCaseForm workflow={wf} busy={busy} onCancel={() => setDialog(null)}
        onSubmit={form => void run('해결 기록을 남겼습니다.', signal => registerResolutionCase(ticketId, { ...form, version }, signal))} />
    </KvModal>}
    {dialog === 'correct' && data && analysisData && <KvModal title={`분류 정정, ${data.external_id ?? data.id.slice(0, 8)}`} onClose={() => setDialog(null)}>
      <AnalysisCorrection analysis={analysisData} onDone={() => setDialog(null)} onSubmit={async body => { try { await correctAnalysis(ticketId, body, new AbortController().signal); setNotice({ kind: 'ok', text: '분류 정정을 저장했습니다.' }); } finally { await analysis.refetch(); } }} />
    </KvModal>}
    {dialog === 'draft' && data && <KvModal title={`답변·정보 요청 문안, ${data.external_id ?? data.id.slice(0, 8)}`} onClose={() => !busy && setDialog(null)}>
      <AiDraftDialog analysis={analysisData} ticketLabel={`${data.customer_external_id}, ${data.body_language}`} send={sendOptions} />
    </KvModal>}
    {dialog === 'owner' && data && <KvModal title={`담당 변경, ${data.external_id ?? data.id.slice(0, 8)}`} onClose={() => !busy && setDialog(null)}>
      <form onSubmit={event => { event.preventDefault(); const next = String(new FormData(event.currentTarget).get('owner') ?? ''); if (next) void run('담당자를 바꿨습니다.', signal => reassignOwner(ticketId, { owner_user_id: next, version }, signal)); }}>
        <div className="kv-field"><span>현재 담당</span><span className="ro">{owner}</span></div>
        <div className="kv-field"><span className="req">새 담당</span>
          {assignees.isPending ? <span className="ro">담당자 목록을 읽는 중입니다</span> : assignees.isError ? <span className="ro">담당자 목록을 불러오지 못했습니다</span>
            : <select name="owner" aria-label="새 담당" required defaultValue=""><option value="" disabled>선택</option>{assignees.data.data.items.filter(item => item.user_id !== wf?.owner_user_id).map(item => <option key={item.user_id} value={item.user_id}>{item.display_name}</option>)}</select>}</div>
        <div className="kv-sub" style={{ margin: '8px 0 0' }}>상태는 바뀌지 않습니다. 새 담당자가 활성 C/S 담당자인지는 서버가 확인합니다.</div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(null)}>취소</button><button type="submit" className="kb primary" disabled={busy || !assignees.isSuccess}>변경</button></div>
      </form>
    </KvModal>}
    {dialog === 'close' && data && <KvModal title={`종결, ${data.external_id ?? data.id.slice(0, 8)}`} onClose={() => !busy && setDialog(null)}>
      <div className="kv-sub" style={{ margin: '0 0 8px' }}>이 문의를 종결합니다. 종결 뒤에는 해결 기록을 한 건 남길 수 있습니다. 종결 조건은 서버가 확인하며, 맞지 않으면 사유를 알려 줍니다.</div>
      <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(null)}>닫기</button><button type="button" className="kb primary" disabled={busy} onClick={() => void run('문의를 종결했습니다.', signal => closeTicket(ticketId, version, signal))}>종결</button></div>
    </KvModal>}
  </main>;
}
