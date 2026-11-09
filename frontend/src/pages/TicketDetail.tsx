import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import TicketAnalysisPanel from '../components/TicketAnalysisPanel';
import AnalysisRunButton, { type RunPhase } from '../components/AnalysisRunButton';
import ResolutionCaseForm, { listFaults } from '../components/ResolutionCaseForm';
import DeptResultReview from '../components/DeptResultReview';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ApiError, getApiMode, request } from '../api/client';
import { getTicket, getTicketAnalysis, getTicketAudit, getTicketSummary, getTicketTranslation, getTicketUnits, type TicketListResponse } from '../api/cs';
import { closeTicket, confirmReturn, correctAnalysis, createTicketSummary, createTicketTranslation, getTicketWorkflow, INVALID_TRANSITION_TEXT, isInvalidOwner, isInvalidTransition, isSendPending, isResolutionExists, isResultVersionConflict, isVersionConflict, reassignOwner, recallTransfer, registerResolutionCase, reviewDeptResult, suggestFault, saveUnitLink, startReview, transferTicket, type DeptCode, type UnitLinkBody, type WorkflowRecord, refreshCustomerSend, retryCustomerSend, sendCustomerMessage, createDeptReplyDraft, getDeptReplyDraft, translateReplyDraft, applyKoreanEdit } from '../api/cs-workflow';
import { listCsAssignees, me } from '../api/platform';
import { listMailboxes } from '../api/mailbox';
import { ErrorState, Loading } from '../components/states';
import CustomerThread, { isUnsettledSend, latestUnansweredReply } from '../components/CustomerThread';
import AiDraftDialog, { OPTION_LABEL, optionsOf, pickDraft, textOf, type SendOptions } from '../components/AiDraftDialog';
import AnalysisCorrection from '../components/AnalysisCorrection';
import ReplyAnalysisCard from '../components/ReplyAnalysisCard';
import { DeptReplyDraft, DraftKorean } from '../components/ReplyDraftAssist';
import { KvGrid, KvModal, useHotkey, type KvColumn } from '../ls-shell/kv';
import { CATEGORY_LABEL, CHANNEL_LABEL, LANGUAGE_LABEL, RETURN_STATED_LABEL, STATE_LABEL, assignmentText, formatStamp, infoName } from '../ls-shell/cs-labels';
import './ticket-detail-v3.css';
import { ElloMark, ElloWorking } from '../components/Ello';

const MOCK_BLOCKED = '모의 응답 모드에서는 실행할 수 없습니다';
const DEPT_LABEL: Record<DeptCode, string> = {
  TS_LV: '저압기기 기술지원', TS_MV: '중압기기 기술지원', TS_TRF: '변압기 기술지원', TS_AUTO: '자동화 기술지원',
  TS_ENERGY: '에너지 기술지원', RND_FW: '펌웨어 개발', QA_CLAIM: '품질보증/클레임', FIELD_SVC: '현장 서비스',
  CERT: '인증', SALES_OPS: '영업 운영', SCM: '주문/물류', WARRANTY_DESK: '보증 데스크',
};
const TRANSFER_STATUS: Record<WorkflowRecord['transfers'][number]['status'], string> = {
  OPEN: '검토 중', RESULT_SUBMITTED: '결과 제출', RETURNED_BY_DEPT: '부서 반송', RECALLED: 'C/S 회수',
};
const URGENCY_LABEL: Record<string, string> = { SAFETY: '안전 위험', SITE_DOWN: '현장 정지', PROD_IMPACT: '생산 영향', COMMISSION: '시운전', DEADLINE: '기한', NONE: '아니오' };
const LANG_LABEL = LANGUAGE_LABEL;
const COMMISSIONING_LABEL: Record<string, string> = { CONFIRMED: '설치 확정', PLANNED: '설치 예정', UNKNOWN: '설치일 모름' };
const UNIT_STATUS_LABEL: Record<string, string> = { ACTIVE: '사용 중', REPLACED: '교체됨', RETIRED: '사용 종료' };
const STEPS = ['접수', '분류 확인', '장비 확인', '고객 회신', '종결'];
type Transfer = WorkflowRecord['transfers'][number];
type Dialog = 'transfer' | 'recall' | 'close' | 'resolution' | 'owner' | 'draft' | 'correct' | 'return' | 'dept' | null;
type Notice = { kind: 'ok' | 'error'; text: string } | null;
const hm = (iso: string) => formatStamp(iso).slice(11, 16);
const md = (iso: string) => formatStamp(iso).slice(5, 10);

// 문의 처리 CS0102. 점검반영 v3 목업(02_CS_문의처리.png) 배치: 상단 정보 8칸, 단계 바와 지금 할 일, 원문·문의 정보·회신 준비 3열.
// 문의가 바뀌면(알림·링크로 같은 화면 안에서 이동) 입력·발송 확인·분석 진행 상태를 모두 새로 시작한다.
// 앞 문의의 수신자·본문·승인 체크가 다음 문의에 남아 그대로 발송되지 않게 하려는 것이다.
// 한국어 요약은 줄마다 한 항목이다(요약 프롬프트 v2). 이전 판본처럼 줄이 없으면 문장 끝(. ! ?)에서 나눈다.
// 줄 앞 번호·글머리표는 화면이 붙이므로 뗀다.
function summaryLines(text: string): string[] {
  const lines = text.includes('\n') ? text.split('\n') : text.split(/(?<=[.!?。])\s+/);
  return lines.map(line => line.replace(/^\s*(?:\d{1,2}[.)]|[-•·*])\s+/, '').trim()).filter(Boolean);
}

export default function TicketDetail() {
  const { ticketId = '' } = useParams();
  return <TicketScreen key={ticketId} ticketId={ticketId} />;
}

function TicketScreen({ ticketId }: { ticketId: string }) {
  const navigate = useNavigate();
  const mode = getApiMode();
  const access = useBusinessAccess('cs');
  const ticket = useQuery({ queryKey: ['tickets', mode, ticketId], queryFn: ({ signal }) => getTicket(ticketId, signal), enabled: access.ready && ticketId !== '' });
  const identity = useQuery({ queryKey: ['ticket-owner-identity', mode, access.user.data?.data?.id], queryFn: me, retry: false, enabled: access.ready });
  const analysisUser = access.user.data?.data ?? identity.data?.data;
  // AI 분석 실행 중에는 결과를 자주 다시 읽어, 먼저 저장된 분류부터 보여 준다(분류 → 초안 → 문장 검증 순으로 저장됨).
  const [runPhase, setRunPhase] = useState<RunPhase>('idle');
  // 이번 화면에서 실행한 분석이 끝났는지. 끝나면 진행 표시를 멈춘 채 남겨 둔다.
  const [runDone, setRunDone] = useState<'ok' | 'fail' | null>(null);
  const runBase = useRef<{ run?: string; draft?: string } | null>(null);
  const analysis = useQuery({ queryKey: ['ticket-analysis', mode, analysisUser?.id, ticketId], queryFn: ({ signal }) => getTicketAnalysis(ticketId, signal), enabled: access.ready && ticketId !== '', retry: false, refetchInterval: runPhase === 'running' ? 1500 : false });
  // 등급을 몰라 표준으로 계산했으면 기한을 읽는 사람이 알아야 한다. 분석 필드가 없어도 안내한다.
  const tierNote = analysis.data?.data?.rules?.tier_note;
  const analysisData = analysis.isSuccess && analysis.data.data && typeof analysis.data.data.fields === 'object' && analysis.data.data.fields ? analysis.data.data : undefined;
  const workflow = useQuery({ queryKey: ['workflow', mode, ticketId], queryFn: ({ signal }) => getTicketWorkflow(ticketId, signal), enabled: access.ready && ticketId !== '' && ticket.isSuccess });
  const summary = useQuery({ queryKey: ['ticket-summary', mode, ticketId], queryFn: ({ signal }) => getTicketSummary(ticketId, signal), enabled: access.ready && ticketId !== '', retry: false, staleTime: 0 });
  const translation = useQuery({ queryKey: ['ticket-translation', mode, ticketId], queryFn: ({ signal }) => getTicketTranslation(ticketId, signal), enabled: access.ready && ticketId !== '', retry: false, staleTime: 0 });
  const units = useQuery({ queryKey: ['ticket-units', mode, ticketId], queryFn: ({ signal }) => getTicketUnits(ticketId, signal), enabled: access.ready && ticketId !== '', retry: false, staleTime: 0 });
  const audit = useQuery({ queryKey: ['ticket-audit', mode, ticketId], queryFn: ({ signal }) => getTicketAudit(ticketId, signal), enabled: access.ready && ticketId !== '', retry: false, staleTime: 0 });
  const customer = ticket.data?.data?.customer_external_id;
  const others = useQuery({ queryKey: ['customer-tickets', mode, customer], queryFn: ({ signal }) => request<TicketListResponse>(`/api/v1/cs/tickets?limit=50&customer=${encodeURIComponent(customer ?? '')}`, { signal }), enabled: access.ready && !!customer, retry: false });
  const assignees = useQuery({ queryKey: ['cs-assignees', mode], queryFn: ({ signal }) => listCsAssignees(signal), enabled: access.ready, retry: false });
  const [dialog, setDialog] = useState<Dialog>(null);
  const [sendStep, setSendStep] = useState(false);
  const faultList = useQuery({ queryKey: ['cs-faults', mode, ticket.data?.data?.product_code], queryFn: ({ signal }) => listFaults(ticket.data!.data.product_code, signal), enabled: dialog === 'resolution' && !!ticket.data?.data?.product_code, retry: false, staleTime: 300000 });
  const mailboxes = useQuery({ queryKey: ['send-mailboxes', mode], queryFn: () => listMailboxes(), enabled: (dialog === 'draft' || sendStep) && mode === 'live', retry: false, staleTime: 0 });
  const [busy, setBusy] = useState(false);
  // AI 요약을 만드는 동안 요약 상자에 회신 초안과 같은 줄 모양 로딩을 보인다.
  const [summarizing, setSummarizing] = useState(false);
  // 한국어 번역을 만드는 동안 한국어 쪽에 같은 줄 모양 로딩을 보인다.
  const [translating, setTranslating] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [returnFormKey, setReturnFormKey] = useState(0);
  // 원문은 항상 보이고, 한국어 번역은 원문 위에 덧붙인다(전환 아님).
  const [showKo, setShowKo] = useState(false);
  // 한국어 보기를 열 때 번역이 없으면 만든다. 저장본 조회가 끝난 뒤에 판단한다(조회 중에 눌러도 된다).
  const [wantKo, setWantKo] = useState(false);
  const translateRef = useRef<(() => void) | null>(null);
  const [draftPick, setDraftPick] = useState<string | undefined>(undefined);
  const [action, setAction] = useState<'info' | 'answer' | 'transfer' | undefined>(undefined);
  // 회신 상자 안에서 고쳐 쓰고(edit) 보내기 확인(step)까지 한다. 고객 메일이라 승인 체크는 꼭 거친다.
  const [edited, setEdited] = useState<string | null>(null);
  // 부서 답변 기반 AI 초안을 회신 상자에 넣었으면 그 글(KAN-251). 회신 상자의 글이 같을 때만 출처를 표시한다.
  const [deptDraftText, setDeptDraftText] = useState<string | null>(null);
  // 비교 창에서 고른 초안 버전. 없으면 다음 행동에 맞는 기본 버전(정보 요청=정보 먼저, 답변=자세하게).
  const [boxPick, setBoxPick] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [sendTo, setSendTo] = useState<string | null>(null);
  const [sendSubject, setSendSubject] = useState<string | null>(null);
  const [sendMailbox, setSendMailbox] = useState('');
  const [askCodes, setAskCodes] = useState<string[] | null>(null);
  // 승인은 승인한 순간의 내용에 묶는다. 본문·받는 사람·제목·사서함·묻는 정보 중 하나라도 바뀌면 승인이 풀린다.
  const [approvedFor, setApprovedFor] = useState<string | null>(null);
  const [closeNoteError, setCloseNoteError] = useState(false);
  const [copyNote, setCopyNote] = useState('');
  const [unitPick, setUnitPick] = useState('');
  const running = useRef<AbortController | null>(null);
  useEffect(() => {
    if (!wantKo || translation.isPending) return;
    setWantKo(false);
    if (translation.isSuccess && !translation.data?.data.translation) translateRef.current?.();
  }, [wantKo, translation.isPending, translation.isSuccess, translation.data]);
  useHotkey('Escape', () => navigate('/cs/tickets'), access.ready && dialog === null);

  if (!access.ready) return <BusinessGate access={access} title="문의 처리" />;
  const data = ticket.isSuccess ? ticket.data.data : undefined;
  const rc = data?.resolution_case;
  const wf = workflow.isSuccess && typeof workflow.data.data?.version === 'number' && Array.isArray(workflow.data.data.transfers) ? workflow.data.data : undefined;
  const replyArrived = latestUnansweredReply(wf);
  const knownState = !!data && typeof data.workflow_state === 'string' && Object.hasOwn(STATE_LABEL, data.workflow_state);
  const state = data ? (knownState ? STATE_LABEL[data.workflow_state] : '확인 필요') : '-';
  const pendingResult = (wf?.dept_results ?? []).filter(item => item.review_status === 'SUBMITTED').at(-1);
  const roundOf = (transferId: string) => wf?.transfers.find(item => item.id === transferId)?.round ?? null;
  const REVIEW_LABEL = { SUBMITTED: '확인 전', ACCEPTED: '확인 완료', REWORK_REQUESTED: '다시 요청' } as const;
  const reviewOf = (transferId: string) => { const found = (wf?.dept_results ?? []).filter(item => item.transfer_id === transferId).at(-1); return found ? REVIEW_LABEL[found.review_status] ?? found.review_status : '-'; };
  const canWrite = mode === 'live';
  // 서버(analysis/routes.py CORRECTABLE_STATES)와 같은 조건: 검토 시작 뒤, 종결 전.
  const correctable = ['IN_REVIEW', 'TRANSFERRED', 'WAITING_CUSTOMER', 'ANSWERED'].includes(ticket.data?.data?.workflow_state ?? '');
  const why = (ok: boolean, note: string) => canWrite ? (wf ? (ok ? undefined : note) : '업무 판본을 읽는 중입니다') : MOCK_BLOCKED;
  const st = data?.workflow_state;
  const isManager = identity.data?.data?.roles?.includes('manager') === true;
  const reasons = {
    start: why(st === 'RECEIVED', '이미 검토중'),
    transfer: why(st === 'IN_REVIEW' && !pendingResult, pendingResult ? '확인 전 답변이 있으면 요청할 수 없습니다' : '검토중인 문의만 요청할 수 있습니다'),
    recall: why(st === 'TRANSFERRED', '진행 중인 요청 없음'),
    close: why((st === 'ANSWERED' || st === 'IN_REVIEW') && !pendingResult, pendingResult ? '확인 전 답변이 있으면 불가' : '종결할 수 없는 상태입니다'),
    owner: !isManager ? 'C/S 관리자만 바꿀 수 있습니다' : why(st !== 'CLOSED', '종결된 문의'),
    resolution: why(st === 'CLOSED' && !rc, rc ? '이미 해결 기록이 있습니다(문의당 한 건)' : '종결 후 한 건'),
  };
  const nameOf = (userId: string | null | undefined) => !userId ? '-'
    : identity.data?.data?.id === userId ? identity.data.data.display_name
    : assignees.data?.data.items.find(item => item.user_id === userId)?.display_name ?? `사용자 ${userId.slice(0, 8)}`;
  const owner = wf ? (wf.owner_user_id ? nameOf(wf.owner_user_id) : '미지정') : '-';

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
        : isInvalidTransition(error) ? INVALID_TRANSITION_TEXT
        : isSendPending(error) ? '결과가 정해지지 않은 발송이 있습니다. 원문 아래 대화에서 "상태 확인"이나 "다시 요청"으로 먼저 확인하세요.'
        : isInvalidOwner(error) ? '새 담당자로 지정할 수 없는 사용자입니다. 활성 C/S 담당자만 지정할 수 있습니다.'
        : isResultVersionConflict(error) ? '부서 답변이 그 사이 바뀌었습니다. 최신 답변을 다시 읽었으니 확인한 뒤 다시 저장하세요.'
        : isResolutionExists(error) ? '이 문의에는 이미 해결 기록이 있습니다. 문의당 한 건만 등록할 수 있습니다.'
        : error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 ? error.message
          : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 상태를 읽었습니다. 반영 여부를 확인하세요.' });
      setDialog(null);
      return false;
    } finally {
      await Promise.allSettled([ticket.refetch(), workflow.refetch(), audit.refetch(), units.refetch()]);
      setBusy(false);
    }
  }
  const version = wf?.version ?? 0;
  const unsettledSend = (wf?.customer_sends ?? []).find(isUnsettledSend);
  const sendBlock = !canWrite ? MOCK_BLOCKED : !wf ? '업무 판본을 읽는 중입니다'
    : st !== 'IN_REVIEW' ? '검토중인 문의만 보낼 수 있습니다' : pendingResult ? '확인 전 부서 답변이 있으면 보낼 수 없습니다'
    : unsettledSend ? '결과가 정해지지 않은 발송이 있습니다. 원문 아래 대화에서 상태를 먼저 확인하세요' : undefined;
  const sendOptions: SendOptions = {
    originalSubject: data?.subject ?? null, defaultTo: wf?.mail_origin?.sender_address ?? null,
    mailboxes: mailboxes.isSuccess ? (mailboxes.data ?? []).filter(item => item.state === 'connected' && !item.reconnect_required).map(item => ({ id: item.mailbox_id, label: `사서함 ${item.mailbox_id.slice(0, 8)}` })) : undefined,
    mailboxesError: mailboxes.isError, defaultMailboxId: wf?.mail_origin?.mailbox_id ?? null, disabled: sendBlock, busy,
    onSend: body => void run(reply => sendNotice(reply), signal => sendCustomerMessage(ticketId, { ...body, version }, signal)),
  };
  function sendNotice(reply: unknown): NonNullable<Notice> {
    const last = (reply as { data?: { customer_sends?: { request_status?: string; request_error?: string | null }[] } } | null)?.data?.customer_sends?.at(-1);
    switch (last?.request_status) {
      case 'REGISTERED': return { kind: 'ok', text: '승인해 플랫폼에 넘겼습니다. 원문 아래 대화의 "상태 확인"으로 접수 여부를 읽으면 문의 상태가 바뀝니다.' };
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

  // 분석 값
  const fields = analysisData?.fields as Record<string, { effective_value?: unknown; corrected?: boolean; source?: string | null } | undefined> | undefined;
  const fv = (key: string) => fields?.[key]?.effective_value;
  const tagOf = (key: string) => fields?.[key]?.corrected ? <span className="ok" title="사람이 정정한 값이 우선합니다. AI를 다시 실행해도 바뀌지 않습니다">✓ 사람 정정</span> : fields?.[key]?.source === 'ai_draft' ? <span className="ai2"><ElloMark size={14} />AI</span> : null;
  const urgency = fv('urgency_flag') as string | undefined;
  const category = fv('category_code') as string | undefined;
  const provided = Array.isArray(fv('provided_info')) ? (fv('provided_info') as unknown[]).map(String) : [];
  const run0 = analysisData?.latest_run ?? undefined;
  const draftRun = pickDraft(analysisData);
  const draftOptions = optionsOf(draftRun);
  const finished = runPhase === 'idle' ? runDone : null;
  const analyzing = runPhase !== 'idle';
  const classArrived = !!runBase.current && !!run0 && run0.id !== runBase.current.run;
  const draftArrived = !!runBase.current && !!draftRun && draftRun.id !== runBase.current.draft;
  const stage = !analyzing ? null : !classArrived ? 0 : !draftArrived ? 1 : 2;
  const waitingClass = analyzing && !classArrived;
  const waitingDraft = analyzing && !draftArrived;
  const reveal = (index: number) => ({ animationDelay: `${index * 90}ms` });
  const rule = analysisData?.rules?.effective ?? undefined;
  // 기획서 4.1 ②~⑥: 다음 행동(추가 정보 요청 / 답변 / 부서 이관) 하나를 정하고 그 초안 하나를 확인해 보낸다.
  const info = analysisData?.missing_info?.effective ?? undefined;
  const missing = info?.missing ?? [];
  // 기획서 4.1 ②: 추가 정보 요청은 처리를 막는 누락(정책표 blocking_fields)만 묻는다. 나머지 누락은 답변에서 같이 물어본다.
  const blocking = info?.blocking_missing ?? [];
  const optional = missing.filter(code => !blocking.includes(code));
  const names = (codes: readonly string[]) => codes.map(infoName).join(', ');
  const asked = info?.already_asked ?? [];
  const escalation = rule?.escalation_dept ?? null;
  // 확인 완료된 부서 답변이 있으면 다음 행동은 그 답변을 반영한 고객 답변이다(빠진 정보가 남아 있어도).
  const acceptedResult = (wf?.dept_results ?? []).filter(item => item.review_status === 'ACCEPTED').at(-1);
  const defaultAction = acceptedResult ? 'answer' : blocking.length > 0 ? 'info' : 'answer';
  // 제안 이유. 이관 조건은 규칙이 판단할 수 없어(예: "불량 반품이면") 자동 제안하지 않고 확인 표시만 한다.
  const suggestWhy = acceptedResult ? '확인 완료된 부서 답변이 있어 답변을 제안합니다'
    : blocking.length > 0 ? `처리에 꼭 필요한 정보 ${blocking.length}개(${names(blocking)})가 빠져 있어 추가 정보 요청을 제안합니다`
      : optional.length > 0 ? `꼭 필요한 정보는 있어 답변을 제안합니다. 빠진 정보(${names(optional)})는 답변에서 같이 물어보세요`
        : '빠진 정보가 없어 답변을 제안합니다';
  const act = action ?? defaultAction;
  const optionOf = (label: string) => draftOptions.find(item => item.label === label);
  const chosen = boxPick === 'OWN' ? undefined : (boxPick ? optionOf(boxPick) : undefined) ?? (act === 'info' ? optionOf('INFO_FIRST') ?? draftOptions[0] : act === 'answer' ? optionOf('DETAILED') ?? optionOf('CONCISE') ?? draftOptions[0] : undefined);
  const verdicts = ([...(draftRun?.verifications ?? [])] as { attempt_number: number; sentences?: { label: string; sentence_index: number; verdict: string }[] }[])
    .sort((a, b) => b.attempt_number - a.attempt_number)[0]?.sentences ?? [];
  const verdictOf = (index: number) => chosen ? verdicts.find(item => item.label === chosen.label && item.sentence_index === index)?.verdict : undefined;
  const safety = urgency === 'SAFETY';
  // 초안을 만든 뒤에 분류를 정정했으면, 초안은 정정 전 분류 기준이다(물을 정보가 다를 수 있다).
  const staleDraft = !!draftRun && (analysisData?.corrections ?? []).some(item => item.created_at > draftRun.created_at);
  const bodyText = edited ?? (chosen ? textOf(chosen) : '');
  // 팀 결정(11/3): 메일 문의만 시스템에서 메일로 보낸다. 다른 채널은 초안을 복사해 그 채널로 직접 응대한다.
  const isMail = ticket.data?.data?.channel === 'EMAIL';
  // 고객 언어가 한국어면 초안 번역이 필요 없다(KAN-251).
  const koreanCustomer = (ticket.data?.data?.body_language ?? '').split('-')[0] === 'ko';
  const channelName = ticket.data?.data ? CHANNEL_LABEL[ticket.data.data.channel] : '';
  const [copied, setCopied] = [copyNote, setCopyNote];
  const copyBody = async () => {
    try { await navigator.clipboard.writeText(bodyText); setCopied('복사했습니다'); }
    catch { setCopied('복사하지 못했습니다. 글을 직접 선택해 복사하세요'); }
  };
  const draftCodes = chosen?.info_requests.map(item => item.info_code) ?? [];
  const codeChoices = [...new Set([...draftCodes, ...missing])];
  const asking = askCodes ?? draftCodes;
  const connected = mailboxes.isSuccess ? (mailboxes.data ?? []).filter(item => item.state === 'connected' && !item.reconnect_required) : undefined;
  const mailboxId = sendMailbox || (connected?.some(item => item.mailbox_id === wf?.mail_origin?.mailbox_id) ? wf?.mail_origin?.mailbox_id ?? '' : connected?.length === 1 ? connected[0].mailbox_id : '');
  const toValue = sendTo ?? wf?.mail_origin?.sender_address ?? '';
  const subjectValue = sendSubject ?? `Re: ${ticket.data?.data?.subject || '문의'}`;
  const recipients = toValue.split(/[\s,;]+/).filter(Boolean);
  const sendKey = JSON.stringify([act, bodyText.trim(), recipients, subjectValue.trim(), mailboxId, act === 'info' ? [...asking].sort() : []]);
  const approved = approvedFor !== null && approvedFor === sendKey;
  const setApproved = (on: boolean) => setApprovedFor(on ? sendKey : null);
  const sendProblem = sendBlock ? sendBlock
    : mailboxes.isError ? '사서함 연결을 읽지 못했습니다'
    : !connected ? '사서함 연결을 읽는 중입니다'
    : connected.length === 0 ? '연결된 사서함이 없습니다. 메일 계정에서 먼저 연결하세요'
    : !mailboxId ? '보낼 사서함을 고르세요'
    : recipients.length === 0 ? '받는 사람을 입력하세요'
    : !recipients.every(item => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(item)) ? '받는 사람 주소 형식을 확인하세요'
    : !subjectValue.trim() ? '제목을 입력하세요'
    : !bodyText.trim() ? '보낼 내용이 비어 있습니다'
    : act === 'info' && asking.length === 0 ? '이 메일이 묻는 정보를 하나 이상 고르세요'
    : !approved ? '내용과 받는 사람을 확인했다고 표시하세요' : '';
  const resetSend = () => { setSendStep(false); setApproved(false); setAskCodes(null); };
  const submitSend = () => {
    if (sendProblem) return;
    const linked = chosen && draftRun && draftRun.id === analysisData?.latest_valid_draft_id;
    void run(reply => sendNotice(reply), signal => sendCustomerMessage(ticketId, {
      version, kind: act === 'info' ? 'INFO_REQUEST' : 'ANSWER', mailbox_id: mailboxId, subject: subjectValue.trim(), body: bodyText.trim(), body_type: 'text', to: recipients,
      requested_info_codes: act === 'info' ? asking as never : [],
      ...(linked ? { draft_id: draftRun.id, draft_option: chosen.label as 'CONCISE' | 'DETAILED' | 'INFO_FIRST' } : {}),
    }, signal)).then(ok => { if (ok) { resetSend(); setEdited(null); setEditing(false); } });
  };
  // 이력에 있는 값은 다시 묻지 않고 확인만 한다(기획서 ②). 확정된 장비의 값만 쓴다.
  const historyValue = (code: string) => !confirmedUnit ? undefined
    : code === 'serial_no' ? confirmedUnit.unit.serial_no : code === 'install_date' ? confirmedUnit.unit.commissioning_date ?? undefined
    : code === 'current_fw' ? confirmedUnit.unit.fw_version_last ?? undefined : undefined;
  // 첫 응답 기한: 접수부터 기한까지 75%가 지나면 위험(기획서 ⑤).
  const due = analysisData?.rules?.first_response_due?.effective ?? rule?.first_response_due ?? null;
  const replied = (wf?.customer_sends ?? []).some(item => item.send_status === 'ACCEPTED');
  const sla = (() => {
    if (!due || !ticket.data?.data) return undefined;
    const start = Date.parse(ticket.data.data.received_at), end = Date.parse(due), now = Date.now();
    const left = end - now;
    const hours = Math.abs(left) / 3600000;
    const span = hours >= 24 ? `${Math.floor(hours / 24)}일 ${Math.round(hours % 24)}시간` : `${Math.max(1, Math.round(hours))}시간`;
    if (replied) return { text: `첫 응답 기한 ${formatStamp(due)} · 응답함`, short: '첫 응답 완료', cls: 'ok' };
    if (left < 0) return { text: `첫 응답 기한 ${formatStamp(due)} · ${span} 지남`, short: `첫 응답 기한 지남`, cls: 'over' };
    const risk = (now - start) / (end - start) >= 0.75;
    return { text: `첫 응답 기한 ${formatStamp(due)} · 남은 ${span}`, short: `${risk ? '위험 · ' : ''}첫 응답 ${span} 남음`, cls: risk ? 'risk' : '' };
  })();

  // 장비
  const unitRows = units.data?.data.items ?? [];
  const confirmedUnit = unitRows.find(row => row.link?.link_status === 'CONFIRMED');
  const pickedUnit = unitRows.find(row => row.unit.id === unitPick) ?? confirmedUnit ?? unitRows.find(row => row.matches_ticket) ?? unitRows[0];
  const saveUnit = (status: UnitLinkBody['link_status']) => {
    if (!pickedUnit) return;
    void run(status === 'CONFIRMED' ? `설치 장비 ${pickedUnit.unit.serial_no}을(를) 확정했습니다.` : `${pickedUnit.unit.serial_no}을(를) 아님으로 기록했습니다.`,
      signal => saveUnitLink(ticketId, pickedUnit.unit.id, { link_status: status, link_basis: 'AGENT_REVIEW', repeat_type: null, version: pickedUnit.link?.version ?? null }, signal));
  };

  // 고객 맥락: 같은 고객의 다른 문의
  const otherTickets = (others.data?.data.items ?? []).filter(item => item.id !== ticketId);
  const openCount = otherTickets.filter(item => item.workflow_state !== 'CLOSED').length;
  const latestOther = otherTickets.map(item => item.received_at).sort().at(-1);

  // 단계
  // 등록된 장비가 없는 고객은 장비 확인 단계가 해당 없다.
  // 장비가 없는 고객이거나 장비 조회가 실패하면 이 단계에서 멈추지 않고 다음 할 일을 안내한다(장비 상자에서 다시 조회).
  const unitsNone = (units.isSuccess && unitRows.length === 0) || units.isError;
  const sent = (wf?.customer_sends ?? []).some(item => item.send_status === 'ACCEPTED');
  const done = [
    knownState && st !== 'RECEIVED',
    !!run0 && run0.outcome === 'VALID',
    !!confirmedUnit,
    sent || st === 'WAITING_CUSTOMER' || st === 'ANSWERED' || st === 'CLOSED',
    st === 'CLOSED',
  ];
  // 장비 확인은 장비가 확정돼야 체크한다. 다만 확정할 장비가 없는 고객은 그 단계에서 멈추지 않고 다음 할 일로 넘어간다.
  const at = done.findIndex((item, index) => !item && !(index === 2 && unitsNone));
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  const now: { label: string; button?: string; act?: () => void; blocked?: string } =
    !knownState ? { label: '현재 상태를 확인할 수 없습니다. 최신 상태를 조회하세요' }
    : st === 'CLOSED' ? (rc ? { label: '처리 완료' } : { label: '해결 기록 남기기', button: '해결 기록', act: () => setDialog('resolution'), blocked: reasons.resolution })
    : pendingResult ? { label: '부서 답변 확인', button: '답변 확인', act: () => setDialog('dept') }
    : st === 'TRANSFERRED' ? { label: '부서 답변 기다리는 중' }
    : st === 'WAITING_CUSTOMER' ? { label: '고객 회신 기다리는 중' }
    : st === 'IN_REVIEW' && acceptedResult && !sent ? (isMail
      ? { label: '부서 답변을 반영해 고객 회신', button: '회신 준비', act: () => { setAction('answer'); scrollTo('td-reply'); } }
      : { label: `${channelName}로 직접 응대한 뒤 종결`, button: '종결', act: () => setDialog('close'), blocked: reasons.close })
    : at === 0 ? { label: '검토 시작', button: '검토 시작', act: () => void doStart(), blocked: reasons.start }
    : at === 1 ? { label: 'AI 분석 실행', button: '분석으로', act: () => scrollTo('td-info') }
    : at === 2 ? { label: '설치 장비 확정', button: '장비 확정', act: () => saveUnit('CONFIRMED'), blocked: !pickedUnit ? '장비 후보가 없습니다' : !pickedUnit.matches_ticket ? '고객·제품이 다른 장비는 확정할 수 없습니다' : !canWrite ? MOCK_BLOCKED : undefined }
    : at === 3 ? (isMail ? { label: '고객 회신', button: '회신 준비', act: () => scrollTo('td-reply') } : { label: `${channelName}로 직접 응대한 뒤 종결`, button: '종결', act: () => setDialog('close'), blocked: reasons.close })
    : { label: '종결', button: '종결', act: () => setDialog('close'), blocked: reasons.close };

  // 처리 기록: 감사 이력 + AI 실행(분석·초안·요약·번역)
  type LogRow = { key: string; at: string; who: string; text: string };
  const changeText = (changes: unknown) => {
    const entries = Object.entries((changes ?? {}) as Record<string, { from?: unknown; to?: unknown }>);
    const moved = entries.find(([key]) => key === 'state');
    if (moved) return `상태 ${STATE_LABEL[moved[1]?.from as keyof typeof STATE_LABEL] ?? moved[1]?.from ?? ''} → ${STATE_LABEL[moved[1]?.to as keyof typeof STATE_LABEL] ?? moved[1]?.to}`;
    if (entries.some(([key]) => key === 'provenance_kind')) return `분류 정정 ${entries.filter(([key]) => ['category_code', 'secondary_category', 'urgency_flag', 'provided_info'].includes(key)).length}개`;
    if (entries.some(([key, value]) => key === 'action' && value?.to === 'auto_assign')) return '자동 배정(규칙)';
    if (entries.some(([key]) => key === 'owner_user_id')) return '담당 변경';
    return entries.slice(0, 3).map(([key]) => key).join(', ') || '기록';
  };
  const logs: LogRow[] = [
    ...(audit.data?.data?.items ?? []).map(item => ({ key: item.id, at: item.occurred_at, who: item.actor_user_id ? nameOf(item.actor_user_id) : 'system', text: changeText(item.changes) })),
    ...(analysisData?.runs ?? []).map(item => ({ key: `run-${item.id}`, at: item.created_at, who: 'ai', text: item.outcome === 'VALID' ? '분류' : `분류 실패 (${item.outcome})` })),
    ...(analysisData?.drafts ?? []).map(item => ({ key: `draft-${item.id}`, at: item.created_at, who: 'ai', text: item.outcome === 'VALID' ? `초안 ${optionsOf(item).length}개` : `초안 실패 (${item.outcome})` })),
    ...(summary.data?.data.summary ? [{ key: 'summary', at: summary.data.data.summary.created_at, who: 'ai', text: '한국어 요약' }] : []),
    ...(translation.data?.data.translation ? [{ key: 'translation', at: translation.data.data.translation.created_at, who: 'ai', text: '한국어 번역' }] : []),
    ...(data ? [{ key: 'received', at: data.received_at, who: 'system', text: `${CHANNEL_LABEL[data.channel]} 수신` }] : []),
  ].sort((a, b) => b.at.localeCompare(a.at));

  const summaryRecord = summary.data?.data.summary;
  // 해결 기록 미리 채우기: 이미 있는 값만 쓴다(AI 생성 아님). 고장 코드·원인·원인 유형·현장 방문·책임 구분은 사람이 적는다.
  const resolutionPrefill = (() => {
    const sources: Record<string, string> = {};
    const out: { symptom?: string; action?: string; dept?: string; answer?: string; fwAt?: string; sources: Record<string, string> } = { sources };
    const symptomText = summaryRecord?.summary_ko ?? data?.subject ?? undefined;
    if (symptomText) { out.symptom = symptomText; sources.symptom = summaryRecord ? '한국어 요약' : '문의 제목'; }
    if (acceptedResult) {
      out.answer = acceptedResult.id; sources.answer = '확인 완료한 부서 답변';
      out.action = acceptedResult.result; sources.action = '부서 답변 내용';
      const deptCode = wf?.transfers.find(item => item.id === acceptedResult.transfer_id)?.dept_code;
      if (deptCode) { out.dept = deptCode; sources.dept = '답변한 부서'; }
    } else if (sent || replied) { out.dept = 'GCS_L1'; sources.dept = 'C/S가 직접 회신'; }
    if (data?.fw_version_reported) { out.fwAt = data.fw_version_reported; sources.fwAt = '고객이 보고한 펌웨어'; }
    return out;
  })();

  // 종결 응대 내용은 검토중에서 확인한 부서 답변·접수된 발송이 없을 때만 필수다(서버 종결 조건과 같다).
  const noteRequired = st === 'IN_REVIEW' && !acceptedResult && !sent;
  const transferColumns: KvColumn<Transfer>[] = [
    { key: 'round', header: '회차', width: 44, className: 'c', cell: item => item.round },
    { key: 'dept', header: '요청 부서', width: 150, cell: item => DEPT_LABEL[item.dept_code as DeptCode] ?? item.dept_code },
    { key: 'status', header: '상태', width: 80, className: 'c', cell: item => TRANSFER_STATUS[item.status] ?? item.status },
    { key: 'reason', header: '사유', cell: item => item.reason },
    { key: 'at', header: '요청 일시', width: 130, className: 'd', cell: item => formatStamp(item.transferred_at) },
    { key: 'review', header: '답변 확인', width: 68, className: 'c', cell: item => reviewOf(item.id) },
  ];
  const summaryOff = summary.error instanceof ApiError && summary.error.status === 404;
  // 한국어 번역(원문/한국어 전환의 한국어 쪽). 꺼진 배포(404)는 안내만 한다. 원문이 한국어면 번역하지 않는다.
  const translationRecord = translation.data?.data.translation;
  const translationOff = translation.error instanceof ApiError && translation.error.status === 404;
  const koSource = (data?.body_language ?? '').split('-')[0] === 'ko';
  const translate = () => { setTranslating(true); void run('번역을 만들었습니다.', signal => createTicketTranslation(ticketId, signal)).then(() => translation.refetch()).finally(() => setTranslating(false)); };
  const translateButton = canWrite && <button className="kb3 sm" disabled={busy || translating || translation.isPending} title="모델을 한 번 호출합니다(비용)" onClick={translate}><ElloMark />다시 번역</button>;
  // "한국어 번역 보기"를 누르면 저장된 번역을 보이고, 없으면 그 자리에서 바로 번역을 만든다(모델 1회). 같은 원문이면 서버가 저장본을 돌려준다.
  const toggleKo = () => {
    const opening = !showKo;
    setShowKo(opening);
    if (opening && canWrite && !translationRecord && !translating && !busy) setWantKo(true);
  };
  translateRef.current = translate;
  const mailCard = (subject: string, body: string, when?: ReactNode) => data && <div className="mail-card">
    <div className="mail-head">
      <span className="avatar" aria-hidden="true">{(wf?.mail_origin?.sender_address ?? data.customer_external_id).slice(0, 1).toUpperCase()}</span>
      <div className="who"><b>{wf?.mail_origin?.sender_address ?? data.customer_external_id}</b><small>{CHANNEL_LABEL[data.channel as keyof typeof CHANNEL_LABEL] ?? data.channel} · {data.customer_external_id}</small></div>
      <span className="when">{when ?? formatStamp(data.received_at)}</span>
    </div>
    <div className="mail-subj">{subject}</div>
    <div className="mail">{body}</div>
    {(data.error_code_reported || data.fw_version_reported) && <div className="mail-tags">
      {data.error_code_reported && <span className="tag"><small>보고된 오류 코드</small>{data.error_code_reported}</span>}
      {data.fw_version_reported && <span className="tag"><small>FW</small>{data.fw_version_reported}</span>}
    </div>}
  </div>;
  const code = data ? data.external_id ?? data.id.slice(0, 8) : '';

  return <main className="td3">
    <div className="title">
      <span className="path">C/S 관리 ›</span><h1>문의 처리</h1><code className="kv-code">CS0102</code>
      <div className="btns">
        <button className="kb3" disabled={busy || reasons.start !== undefined} title={reasons.start} onClick={() => void doStart()}>검토 시작</button>
        <button className="kb3" disabled={busy || reasons.transfer !== undefined} title={reasons.transfer} onClick={() => setDialog('transfer')}>부서 검토 요청</button>
        <button className="kb3" disabled={busy || reasons.recall !== undefined} title={reasons.recall} onClick={() => setDialog('recall')}>요청 취소</button>
        <span className="vsep" />
        <button className="kb3" disabled={busy || reasons.close !== undefined} title={reasons.close ?? '확인 완료된 검토 답변 또는 고객 발송 기록 필요'} onClick={() => setDialog('close')}>종결</button>
        <button className="kb3" disabled={busy || reasons.resolution !== undefined} title={reasons.resolution ?? '종결 후 한 건'} onClick={() => setDialog('resolution')}>해결 기록 남기기</button>
        <span className="vsep" />
        <button className="kb3 pri" disabled title="저장할 항목이 아직 없습니다">저장 <kbd>F10</kbd></button>
        <Link className="kb3" to="/cs/tickets">목록 <kbd>Esc</kbd></Link>
      </div>
    </div>

    {ticket.isPending && <Loading />}
    {ticket.isError && <div className="body">
      <ErrorState error={ticket.error} onRetry={() => void ticket.refetch()} />
      {/* 권한 오류(401·403)는 다시 시도 버튼이 없다. 세션을 고친 뒤 같은 화면에서 다시 읽을 수 있게 둔다. */}
      <div><button className="kb3 sm ghost" disabled={ticket.isFetching} onClick={() => { void ticket.refetch(); void workflow.refetch(); }}>최신 상태 조회</button></div>
    </div>}
    {data && <div className="body">
      {tierNote && <div className="tier-note" role="note">{tierNote}</div>}
      {notice && <div className={notice.kind === 'error' ? 'note err' : 'note'} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</div>}
      <div className="info">
        <div className="k">문의번호</div><div className="v">{code}{urgency && urgency !== 'NONE' && <span className="urg">긴급</span>}</div>
        <div className="k">접수일시</div><div className="v">{formatStamp(data.received_at)}</div>
        <div className="k">고객</div><div className="v"><Link className="link" to={`/cs/customers?customer=${encodeURIComponent(data.customer_external_id)}`}>{data.customer_external_id}</Link></div>
        <div className="k">접수 경로</div><div className="v last">{CHANNEL_LABEL[data.channel]}, {LANG_LABEL[data.body_language] ?? data.body_language}</div>
        <div className="k nb">제품</div><div className="v nb">{data.product_code}</div>
        <div className="k nb">상태</div><div className="v nb"><span className={`wf-state s-${knownState ? data.workflow_state : "UNKNOWN"}`}><i aria-hidden="true" />{state}</span></div>
        <div className="k nb">담당</div><div className="v nb">{owner}
          {/* KAN-214: 지금 담당이 규칙으로 정해졌을 때만 서버가 근거를 준다. 사람이 바꾼 담당이면 없다. */}
          {wf?.assignment && <small className="assign-why" title={`${assignmentText(wf.assignment)} · ${wf.assignment.trigger === 'BATCH' ? '관리자 일괄 배정' : '접수 시 자동'} · 규칙 ${wf.assignment.rule_version}`}>{assignmentText(wf.assignment)}</small>}
          <button className="mini" disabled={busy || reasons.owner !== undefined} title={reasons.owner} onClick={() => setDialog('owner')}>담당 변경</button></div>
        <div className="k nb">고객 맥락</div><div className="v nb last">{others.isSuccess ? `진행 ${openCount} · 장비 ${unitRows.length} · 최근 ${latestOther ? md(latestOther) : '-'}` : '-'}</div>
      </div>

      <div className="steps">
        {STEPS.map((name, index) => {
          const cls = done[index] ? 'done' : index === at ? 'cur' : '';
          return <span key={name} className="sw">{index > 0 && <span className={done[index - 1] ? 'ln' : 'ln g'} />}<span className={`st ${cls}`}><span className="dot">{done[index] ? '✓' : index + 1}</span>{name}</span></span>;
        })}
        <button className="kb3 sm ghost" style={{ marginLeft: 18 }} disabled={ticket.isFetching || busy} onClick={() => { void ticket.refetch(); void workflow.refetch(); void audit.refetch(); void units.refetch(); }}>최신 상태 조회</button>
        {replyArrived && <span className="reply"><b>고객 회신 도착</b> {formatStamp(replyArrived.received_at)}{replyArrived.reopened ? ', 검토중으로 복귀' : ''}</span>}
        <div className="now"><div><small>지금 할 일</small><b>{now.label}</b></div>
          {now.act && <button className="kb3 pri big" disabled={busy || now.blocked !== undefined} title={now.blocked} onClick={now.act}>{now.button} →</button>}</div>
      </div>

      <div className="cols">
        <div className="pane">
          <div className="ph">고객 원문 <div className="r">
            {/* AI 요약 만들기는 한국어 번역 보기 옆에 둔다. 이미 요약이 있으면 단추를 숨긴다(같은 원문은 다시 만들어도 같은 결과). */}
            {!summaryRecord && !summaryOff && canWrite && <button className="kb3 sm" disabled={busy || summary.isPending} title="모델을 한 번 호출합니다(비용)" onClick={() => { setSummarizing(true); void run('요약을 만들었습니다.', signal => createTicketSummary(ticketId, signal)).then(() => summary.refetch()).finally(() => setSummarizing(false)); }}><ElloMark />{summarizing ? '요약 중…' : 'AI 요약'}</button>}
            {!koSource && <button className={showKo ? 'kb3 sm on' : 'kb3 sm'} aria-pressed={showKo} title={!translationRecord && canWrite ? '번역이 없으면 바로 만듭니다(모델 1회 호출)' : undefined} onClick={toggleKo}>{showKo ? '한국어 번역 숨기기' : '한국어 번역 보기'}</button>}</div></div>
          <div className="pb">
            <div className="sum">
              <div className="h"><span className="ai"><ElloMark />AI 요약</span><span className="meta">{summaryRecord ? hm(summaryRecord.created_at) : ''}</span></div>
              {summarizing && !summaryRecord ? <div className="sk-lines" aria-busy="true"><span><ElloWorking />AI가 원문을 읽고 요약하는 중…</span><i /><i /><i /></div>
                : summaryRecord ? summaryLines(summaryRecord.summary_ko).map((line, index) => <p key={index}><span className="n">{index + 1}</span>{line}</p>)
                : summaryOff ? <p className="meta">요약 기능이 이 배포에서 꺼져 있습니다</p>
                : summary.isPending ? <p className="meta">불러오는 중</p>
                : <p className="meta">아직 요약이 없습니다{canWrite ? '. 위의 "AI 요약"을 누르면 만듭니다.' : ''}</p>}
            </div>
            {showKo && !koSource && <div className="ko-add">
              {translating ? <div className="sk-lines" aria-busy="true"><span><ElloWorking />AI가 원문을 한국어로 옮기는 중…</span><i /><i /><i /></div>
                : translationRecord ? <>
                  {translationRecord.stale && <div className="meta" style={{ marginBottom: 6 }}>원문이 바뀌어 다시 번역할 수 있습니다 {translateButton}</div>}
                  {mailCard(translationRecord.subject_ko || '(제목 없음)', translationRecord.body_ko, <><span className="ai"><ElloMark />AI 번역</span> {hm(translationRecord.created_at)}</>)}
                  <div className="meta" style={{ marginTop: 6 }}>AI 번역은 참고용입니다. 숫자·코드·판단은 아래 원문이 기준입니다.</div>
                </>
                : translationOff ? <div className="meta">번역 기능이 이 배포에서 꺼져 있습니다</div>
                : translation.isPending ? <div className="meta">불러오는 중</div>
                : <div className="meta">아직 번역이 없습니다{canWrite ? '' : '. 실제 API에서 C/S 담당자가 열면 만들어집니다'}</div>}
            </div>}
            {/* 메일처럼 보이는 원문 카드: 보낸 사람·경로·시각, 제목, 본문, 고객이 적은 오류 코드·FW. 항상 보인다. */}
            <div className="orig-always"><div className="meta">원문</div>{mailCard(data.subject || '(제목 없음)', data.body)}</div>
            {wf && (wf.customer_replies ?? []).some(item => item.body) && <ReplyAnalysisCard ticketId={ticketId} replies={wf.customer_replies ?? []} provided={provided}
              canCreate={canWrite && !!analysisUser?.roles?.includes('cs_agent')}
              apply={!canWrite ? MOCK_BLOCKED : !analysisData ? 'AI 분류를 읽은 뒤에 반영할 수 있습니다' : !correctable ? '검토중인 문의에서만 반영할 수 있습니다'
                : async (codes, reason) => {
                  try { await correctAnalysis(ticketId, { version: analysisData.version, analysis_id: analysisData.latest_valid_run_id ?? null, reason, provided_info: codes }, new AbortController().signal); setNotice({ kind: 'ok', text: '답한 정보를 제공 정보에 반영했습니다.' }); }
                  finally { await Promise.allSettled([analysis.refetch(), audit.refetch()]); }
                }}
              onNote={(kind, text) => setNotice({ kind, text })} />}
            {wf && ((wf.customer_sends ?? []).length > 0 || (wf.customer_replies ?? []).length > 0) && <div className="thread">
              <div className="meta" style={{ marginBottom: 4 }}>이후 대화</div>
              <CustomerThread original={data} workflow={wf} actions={sendActions} />
            </div>}
          </div>
        </div>

        <div className="pane" id="td-info">
          <div className="ph">문의 정보<div className="r">
            {analysisUser?.id && <AnalysisRunButton actorId={analysisUser.id} ticketId={ticketId} disabled={st === 'CLOSED' || !canWrite || !analysisUser.roles?.includes('cs_agent')}
              title={st === 'CLOSED' ? '종결된 문의는 분석할 수 없습니다' : !analysisUser.roles?.includes('cs_agent') ? 'C/S 담당자만 실행할 수 있습니다' : `분류·검색·초안을 새로 만듭니다(모델 호출 비용).${run0 ? ` 마지막 분석 ${formatStamp(run0.created_at)}` : ''}`}
              onNote={(kind, text) => { setNotice({ kind, text }); setRunDone(kind === 'ok' ? 'ok' : 'fail'); }}
              onPhase={phase => { if (phase === 'sending') setRunDone(null); if (phase === 'sending') runBase.current = { run: run0?.id, draft: draftRun?.id }; setRunPhase(phase); }}
              onComplete={() => { void analysis.refetch(); void ticket.refetch(); void workflow.refetch(); void audit.refetch(); }} />}
            <button className="kb3 sm" disabled={!analysisData || !canWrite || busy || !correctable} title={!analysisData ? 'AI 분류를 읽은 뒤에 정정할 수 있습니다' : !correctable ? (st === 'RECEIVED' ? '검토 시작 후 정정할 수 있습니다' : '종결된 문의는 정정할 수 없습니다') : 'AI 분류를 사유와 함께 정정합니다'} onClick={() => setDialog('correct')}>분류 정정</button></div></div>
          <div className="pb">
            {(analyzing || finished) && <div className={`ai-progress${analyzing ? '' : ' stopped'}${finished === 'fail' ? ' failed' : ''}`} role="status" aria-live="polite">
              <ElloWorking still={!analyzing} />
              <b>{analyzing ? (stage === 0 ? 'AI가 문의를 읽고 분류하는 중' : stage === 1 ? '분류 완료 · 근거를 찾아 초안 쓰는 중' : '초안 완료 · 문장 근거를 검증하는 중') : finished === 'ok' ? 'AI 분석 완료' : 'AI 분석이 끝나지 못했습니다'}</b>
              <ol aria-label="분석 단계">{['분류', '초안', '검증'].map((name, index) => { const ok = finished === 'ok' || (stage !== null && index < stage); return <li key={name} className={ok ? 'done' : index === stage ? 'cur' : ''}>{ok ? '✓ ' : ''}{name}</li>; })}</ol>
            </div>}
            {!run0 && !analyzing && <div className="meta">아직 AI 분석이 없습니다. 위의 "AI 분석 실행"을 누르세요.</div>}
            <div className="fr"><span className="k">주분류</span>{waitingClass ? <div className="box sk" aria-busy="true"><i /></div> : <div className="box reveal" key={`c-${run0?.id}`} style={reveal(0)}>{category ? CATEGORY_LABEL[category] ?? category : '-'}{tagOf('category_code')}</div>}</div>
            <div className="fr"><span className="k">보조분류</span>{waitingClass ? <div className="box sk" aria-busy="true"><i /></div> : <div className="box reveal" key={`s-${run0?.id}`} style={reveal(1)}>{(fv('secondary_category') as string | null) ?? '-'}{tagOf('secondary_category')}</div>}</div>
            <div className="fr"><span className="k">긴급 여부</span>{waitingClass ? <div className="box sk" aria-busy="true"><i /></div> : <div className="box reveal" key={`u-${run0?.id}`} style={reveal(2)} title={rule ? `우선순위 ${rule.priority} · 첫 응답 기한 ${formatStamp(analysisData?.rules?.first_response_due?.effective ?? rule.first_response_due)}` : undefined}>{urgency ? (urgency === 'NONE' ? '아니오' : `예 · ${URGENCY_LABEL[urgency] ?? urgency}`) : '-'}{tagOf('urgency_flag')}</div>}</div>
            <div className="fr"><span className="k">받은 자료</span>{waitingClass ? <div className="chips sk-chips" aria-busy="true"><i /><i /><i /></div> : <div className="chips reveal" key={`p-${run0?.id}`} style={reveal(3)}>
              {provided.map(item => <span key={item} className="chip" title={item}>✓ {infoName(item)}</span>)}
              {missing.map(item => <span key={item} className="chip w" title={`${item} · 아직 받지 못함`}>✕ {infoName(item)}</span>)}
              {provided.length === 0 && missing.length === 0 && <span className="meta">-</span>}
            </div>}</div>
            <div className="fr"><span className="k">반품 유형</span>
              {waitingClass ? <div className="box sk" aria-busy="true"><i /></div> : category === 'RETURN'
                ? <div className="box">{analysisData?.return_subtype_stated ? `${RETURN_STATED_LABEL[analysisData.return_subtype_stated]} (고객 주장)` : '고객 주장 없음'}
                  <button className="mini" onClick={() => setDialog('return')}>{wf?.return_responsibility ? '확정 보기' : '반품 확정'}</button></div>
                : <div className="box dash">해당 없음</div>}
            </div>
            {/* 설치 장비 확인(4.3): 문의 정보 칸들 아래에 둔다. 다른 상자와 같은 얇은 테두리. */}
            <div className="unit" id="td-unit">
              <div className="uh">설치 장비 <span className="ai">후보 {unitRows.filter(row => row.matches_ticket).length}</span><span className="r">{confirmedUnit ? `확정: ${confirmedUnit.unit.serial_no}` : `고객 보유 장비 ${unitRows.length}대 중`}</span></div>
              {units.isPending && <div className="meta">불러오는 중</div>}
              {units.isError && <div className="meta" role="alert">장비 정보를 불러오지 못했습니다 <button className="kb3 sm" onClick={() => void units.refetch()}>다시 조회</button></div>}
              {units.isSuccess && unitRows.length === 0 && <div className="meta">조회 가능한 장비가 없습니다</div>}
              <div className="cand-list">{unitRows.map(row => <label key={row.unit.id} className={row.unit.id === pickedUnit?.unit.id ? 'cand sel' : 'cand'}>
                <input type="radio" name="unit" className="rad" checked={row.unit.id === pickedUnit?.unit.id} onChange={() => setUnitPick(row.unit.id)} />
                <div><b>{row.unit.serial_no}</b><small>{row.unit.product_code} · {COMMISSIONING_LABEL[row.unit.commissioning_status] ?? row.unit.commissioning_status}{row.unit.commissioning_date ? ` ${row.unit.commissioning_date}` : ''}{row.unit.unit_status && row.unit.unit_status !== 'ACTIVE' ? ` · ${UNIT_STATUS_LABEL[row.unit.unit_status] ?? row.unit.unit_status}` : ''}</small></div>
                <span className="why">{row.link?.link_status === 'CONFIRMED' ? '확정됨' : row.link?.link_status === 'REJECTED' ? '아님' : row.matches_ticket ? '고객·제품 일치' : '고객·제품 다름'}</span>
              </label>)}</div>
              {pickedUnit && <div className="uact"><span className="meta">맞는 장비 하나만 확정하면 됩니다. 나머지 후보는 그대로 두세요.</span><button className="kb3 sm" disabled={busy || !canWrite} title="고객이 아니라고 한 경우처럼, 고른 장비가 이 문의의 장비가 아니라는 근거가 있을 때만 기록합니다" onClick={() => saveUnit('REJECTED')}>이 장비 아님</button></div>}
            </div>
          </div>
        </div>

        <div className="stack">
          <div className="pane" id="td-reply">
            <div className="ph">고객 회신 준비{sla && st !== 'CLOSED' && <span className={`sla ${sla.cls}`} title={sla.text}>{sla.short}</span>}</div>
            <div className="pb" style={{ gap: 9 }}>
              {safety && !analysisData?.latest_valid_draft_id && <div className="note err" role="alert"><b>안전 문의입니다.</b> 초안 없이 담당자가 바로 응답하세요.</div>}
              <div className="act" role="radiogroup" aria-label="다음 행동">
                <div className="meta">다음 행동 <small>규칙이 제안하고 담당자가 정합니다</small></div>
                {([
                  ['info', '추가 정보 요청', info ? (missing.length ? `빠진 정보 ${missing.length}개${blocking.length ? ` · 꼭 필요 ${blocking.length}개` : ''}` : '빠진 정보 없음') : '분석 전'],
                  ['answer', '답변', acceptedResult ? '부서 답변 확인됨 · 반영해 답변' : '문서 근거로 답변'],
                  ['transfer', '부서 이관', escalation ? `후보: ${DEPT_LABEL[escalation as DeptCode] ?? escalation}` : '규칙상 후보 없음'],
                ] as const).map(([key, name, sub]) => <label key={key} className={act === key ? 'cand sel' : 'cand'}>
                  <input type="radio" name="act" className="rad" checked={act === key} onChange={() => { setAction(key); setEdited(null); setEditing(false); setBoxPick(null); resetSend(); setCopyNote(''); }} />
                  <div><b>{name}</b><small>{sub}</small></div>
                  {defaultAction === key && <span className="why sug" title={suggestWhy}>제안</span>}
                  {key === 'transfer' && escalation && !acceptedResult && <span className="why chk" title={rule?.escalation_condition ?? ''}>조건 확인</span>}
                </label>)}
              </div>

              <div className="why-line">{suggestWhy}.{escalation && !acceptedResult && rule?.escalation_condition ? ` 이관 조건("${rule.escalation_condition}")에 해당하면 부서 이관을 고르세요.` : ''}</div>
              {act === 'answer' && acceptedResult && <div className="dept-ans">
                <div className="meta"><b style={{ color: '#1d2433' }}>확인한 부서 답변</b> <small>{DEPT_LABEL[(wf?.transfers.find(item => item.id === acceptedResult.transfer_id)?.dept_code ?? '') as DeptCode] ?? ''} · {formatStamp(acceptedResult.submitted_at)}</small></div>
                <div className="dept-body">{acceptedResult.result}</div>
                {acceptedResult.reply_content
                  ? <div className="dept-reply"><span>부서가 제안한 고객 문안</span>{acceptedResult.reply_content}
                    <button className="kb3 sm" onClick={() => { setEdited(acceptedResult.reply_content ?? ''); setEditing(false); }}>이 문안 쓰기</button></div>
                  : <div className="meta">부서가 고객 문안은 주지 않았습니다. 아래 초안을 부서 답변에 맞게 고쳐 쓰거나 AI 초안을 만드세요.</div>}
                {canWrite && <DeptReplyDraft key={acceptedResult.id} create={signal => createDeptReplyDraft(ticketId, acceptedResult.id, signal)} load={signal => getDeptReplyDraft(ticketId, acceptedResult.id, signal)}
                  onUse={text => { setBoxPick('OWN'); setEdited(text); setDeptDraftText(text); setEditing(false); resetSend(); setCopyNote(''); setNotice({ kind: 'ok', text: '부서 답변 기반 AI 초안을 회신 상자에 넣었습니다. 읽고 고친 뒤 보내세요.' }); }} />}
              </div>}
              {act === 'info' && <div className="need">
                <div className="meta">요청할 정보 <small>{info ? `필요 ${info.required.length} · 받음 ${info.provided.length} · 이미 물음 ${asked.length}` : '분석 전'}</small></div>
                {missing.length === 0 && <div className="meta">더 물을 정보가 없습니다. 답변을 고르세요.</div>}
                {(missing.length > 0 || asked.length > 0) && <div className="chips">
                  {missing.map(code => { const known = historyValue(code); return <span key={code} className={known ? 'chip' : 'chip w'} title={known ? `${code} · 이력 값 ${known}, 확인만 요청` : code}>{known ? `확인만 · ${infoName(code)}` : infoName(code)}</span>; })}
                  {asked.map(code => <span key={code} className="chip x" title={`${code} · 이미 물었으므로 다시 묻지 않음`}>{infoName(code)} · 이미 물음</span>)}
                </div>}
              </div>}

              {act === 'transfer'
                ? <div className="xfer">
                  <div className="xfer-head"><span>받는 부서</span><b>{escalation ? (DEPT_LABEL[escalation as DeptCode] ?? escalation) : rule ? (DEPT_LABEL[rule.dept as DeptCode] ?? rule.dept) : '-'}</b><em>{escalation ? '이관 규칙 후보' : rule ? '담당 부서 규칙' : '규칙 결과 없음'}</em></div>
                  {rule?.escalation_condition && <div className="xfer-cond"><span>이관 조건</span>{rule.escalation_condition}<small>조건에 해당하는지는 담당자가 원문을 보고 판단합니다</small></div>}
                  <div className="xfer-list"><span>함께 넘어가는 것</span>
                    <div className="chips"><span className="chip x">고객 원문</span><span className="chip x">AI 분류 · {category ? CATEGORY_LABEL[category] ?? category : '-'}</span><span className="chip x">첫 응답 기한 {due ? formatStamp(due) : '-'}</span>{confirmedUnit && <span className="chip x">확정 장비 {confirmedUnit.unit.serial_no}</span>}</div>
                  </div>
                  <div className="xfer-note">요청 사유는 다음 창에서 적습니다. 부서가 답하면 "부서 답변 확인"이 나타납니다.</div>
                </div>
                : <div className="draft">
                  <div className="meta">초안{edited !== null && edited === deptDraftText ? ' · 부서 답변 기반 AI 초안' : chosen ? ` · ${OPTION_LABEL[chosen.label] ?? chosen.label}` : edited !== null ? ' · 직접 쓰기' : ''} <small>고객 언어: {LANG_LABEL[data.body_language] ?? data.body_language}{draftRun ? ` · AI ${hm(draftRun.created_at)}` : ''}</small></div>
                  {staleDraft && <div className="stale">이 초안은 분류 정정 <b>전에</b> 만들어졌습니다. 묻는 정보가 지금 분류와 다를 수 있으니 "AI 분석 실행"으로 새로 만드세요.</div>}
                  {waitingDraft && !editing
                    ? <div className="prev sk-lines" aria-busy="true"><span><ElloWorking />{classArrived ? '분류를 바탕으로 초안을 쓰는 중…' : '분류가 끝나면 초안을 씁니다'}</span><i /><i /><i /></div>
                    : editing
                    ? <textarea className="edit-body" aria-label="보낼 내용" rows={7} value={bodyText} onChange={event => { setEdited(event.target.value); setApproved(false); }} />
                    : edited !== null ? <div className="prev">{edited}<div className="meta" style={{ marginTop: 4 }}>담당자가 고친 내용</div></div>
                    : !chosen
                    ? <div className="meta">{draftRun ? 'AI 초안을 쓸 수 없습니다. "수정"에서 직접 쓸 수 있습니다.' : '아직 AI 초안이 없습니다. 분석을 실행하면 만들어집니다.'}</div>
                    : <div className="prev">{chosen.sentences.map((item, index) => { const verdict = verdictOf(index); return <span key={index} className="sent">{item.text}{' '}
                      <span className={`tag ${item.kind.toLowerCase()}`}>{item.kind === 'EVIDENCE' ? `근거 ${item.evidence_chunk_ids.length}` : item.kind === 'CONDITION' ? '조건' : '확인할 수 없음'}</span>
                      {verdict && verdict !== 'SUPPORTED' && <span className="tag warn" title={verdict}>검증 필요</span>}{' '}</span>; })}</div>}
                  {chosen && <div className="meta">근거 {new Set(chosen.sentences.flatMap(item => item.evidence_chunk_ids)).size}개 · 문서 이름은 아직 응답에 없음</div>}
                  {canWrite && !waitingDraft && !koreanCustomer && bodyText.trim() !== '' && <DraftKorean key={ticketId} text={bodyText} language={LANG_LABEL[data.body_language] ?? data.body_language}
                    translate={(text, signal) => translateReplyDraft(ticketId, text, signal)} apply={(text, editedKo, signal) => applyKoreanEdit(ticketId, text, editedKo, signal)}
                    onAccept={text => { setEdited(text); setEditing(false); resetSend(); setCopyNote(''); setNotice({ kind: 'ok', text: '한국어 수정을 반영한 초안을 회신 상자에 넣었습니다. 보내기 전에 다시 확인하세요.' }); }} />}
                </div>}

              <div className="warn-note">RMA·방문 일정·보증 승인은 초안에 넣지 않고 담당자가 확정합니다</div>
              {!isMail && act !== 'transfer' && <div className="chan-note">
                <b>{channelName}</b>로 들어온 문의입니다. 시스템에서 메일을 보내지 않습니다. 문안을 복사해 {channelName}로 직접 응대하세요.
                <span>직접 응대한 뒤 [종결]에서 응대 내용을 적으면 종결 기록에 남습니다.</span>
              </div>}
              {sendStep && isMail && act !== 'transfer' && <div className="sendbox" id="td-send">
                <div className="meta"><b style={{ color: '#1d2433' }}>보내기 확인</b> <small>{act === 'info' ? '추가 정보 요청' : '답변'} · 승인해야 나갑니다</small></div>
                <div className="fr"><span className="k">사서함</span><select aria-label="보낼 사서함" value={mailboxId} onChange={event => { setSendMailbox(event.target.value); setApproved(false); }}>
                  <option value="" disabled>{connected ? (connected.length ? '선택' : '연결된 사서함 없음') : '읽는 중'}</option>{(connected ?? []).map(item => <option key={item.mailbox_id} value={item.mailbox_id}>사서함 {item.mailbox_id.slice(0, 8)}</option>)}</select></div>
                <div className="fr"><span className="k">받는 사람</span><input type="text" aria-label="받는 사람" value={toValue} placeholder="여러 명이면 쉼표로 구분" onChange={event => { setSendTo(event.target.value); setApproved(false); }} /></div>
                <div className="fr"><span className="k">제목</span><input type="text" aria-label="제목" value={subjectValue} onChange={event => { setSendSubject(event.target.value); setApproved(false); }} /></div>
                {act === 'info' && <div className="fr top"><span className="k">묻는 정보</span><div className="chips">
                  {codeChoices.length === 0 && <span className="meta">-</span>}
                  {codeChoices.map(code => <label key={code} className={asking.includes(code) ? 'chip w pick' : 'chip x pick'} title={code}>
                    <input type="checkbox" checked={asking.includes(code)} onChange={event => { setAskCodes(event.target.checked ? [...asking, code] : asking.filter(item => item !== code)); setApproved(false); }} />{infoName(code)}</label>)}
                </div></div>}
                <label className="approve"><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)} />내용과 받는 사람을 확인했고, 고객에게 메일이 나가는 것을 승인합니다.</label>
                <div className="row2">
                  <span className="meta">{sendProblem}</span>
                  <button className="kb3 sm" style={{ marginLeft: 'auto' }} disabled={busy} onClick={resetSend}>취소</button>
                  <button className="kb3 sm pri" disabled={busy || !!sendProblem} onClick={submitSend}>{busy ? '보내는 중' : '승인하고 보내기'}</button>
                </div>
              </div>}
              {!sendStep && <div className="row2">
                {act !== 'transfer' && <button className="kb3 sm" disabled={!bodyText && !editing && !chosen} onClick={() => { if (editing) setEditing(false); else { setEdited(bodyText); setEditing(true); } }}>{editing ? '고치기 끝' : '수정'}</button>}
                {act !== 'transfer' && edited !== null && !editing && <button className="kb3 sm ghost" onClick={() => setEdited(null)}>초안으로 되돌리기</button>}
                <button className="kb3 sm ghost" title={draftOptions.length ? 'AI가 만든 초안 3개(간결·자세·정보 먼저)와 문장별 근거·검증 결과를 자세히 봅니다' : '초안이 없어도 직접 써서 보낼 수 있습니다'} onClick={() => { setDraftPick(chosen?.label); setDialog('draft'); }}>다른 초안</button>
                {act === 'transfer'
                  ? <button className="kb3 sm pri" style={{ marginLeft: 'auto' }} disabled={busy || reasons.transfer !== undefined} title={reasons.transfer} onClick={() => setDialog('transfer')}>이관 확정 → 부서 검토 요청</button>
                  : !isMail
                  ? <>{copied && <span className="meta copied">{copied}</span>}<button className="kb3 sm pri" style={{ marginLeft: copied ? 0 : 'auto' }} disabled={!bodyText.trim()} onClick={() => void copyBody()}>문안 복사</button></>
                  : <button className="kb3 sm pri" style={{ marginLeft: 'auto' }} disabled={!!sendBlock || !bodyText.trim()} title={sendBlock ?? '받는 사람·사서함을 확인하고 승인합니다'} onClick={() => { setEditing(false); setSendStep(true); setTimeout(() => document.getElementById('td-send')?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }), 0); }}>{act === 'info' ? '정보 요청 보내기' : '답변 보내기'}</button>}
              </div>}
            </div>
          </div>
          <div className="pane grow">
            <div className="ph">처리 기록</div>
            <div className="logwrap"><table className="log"><thead><tr><th style={{ width: 62 }}>일시</th><th style={{ width: 72 }}>처리자</th><th>내용</th></tr></thead>
              <tbody>{logs.slice(0, 12).map(item => <tr key={item.key} className={item.who === 'ai' ? 'by-ai' : undefined}><td title={formatStamp(item.at)}>{hm(item.at)}</td>
                <td className={item.who === 'ai' ? 'ai' : ''}>{item.who === 'ai' ? <><ElloMark size={14} />AI</> : item.who === 'system' ? '시스템' : item.who}</td><td>{item.text}</td></tr>)}</tbody></table></div>
          </div>
        </div>
      </div>

      {(wf?.transfers.length ?? 0) > 0 || rc ? <div className="cols2" id="td-dept">
        {wf && wf.transfers.length > 0 && <div className="pane"><div className="ph">부서 검토 요청 이력</div>
          <KvGrid label="검토 요청 이력" columns={transferColumns} rows={wf.transfers} rowKey={item => item.id} fallback="요청 이력 없음" /></div>}
        {rc && <div className="pane"><div className="ph">해결 기록</div><div className="pb meta" style={{ color: 'inherit' }}>
          사례 {rc.external_id ?? '-'}. 증상 {rc.symptom}. 원인 {rc.root_cause} ({rc.root_cause_type}). 조치 {rc.action_taken}. 처리 부서 {rc.resolved_by_dept}. 현장 방문 {rc.onsite_visit ? '있음' : '없음'}. 재발 방지 {rc.recurrence_prevention ?? '-'}</div></div>}
      </div> : null}
    </div>}

    {dialog === 'transfer' && data && <KvModal title={`부서 검토 요청, ${code}`} onClose={() => !busy && setDialog(null)}>
      <form onSubmit={submitTransfer}>
        <div className="kv-field"><span>현재</span><span className="ro">{state}, 담당 {owner}</span></div>
        <div className="kv-field"><span className="req">요청 부서</span>
          <select name="dept" aria-label="요청 부서" required defaultValue={escalation && escalation in DEPT_LABEL ? escalation : ''}><option value="" disabled>선택</option>{(Object.keys(DEPT_LABEL) as DeptCode[]).map(item => <option key={item} value={item}>{DEPT_LABEL[item]} ({item})</option>)}</select></div>
        <div className="kv-field"><span className="req">요청 사유</span><textarea name="reason" aria-label="요청 사유" rows={3} required /></div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(null)}>취소</button><button type="submit" className="kb primary" disabled={busy}>요청</button></div>
      </form>
    </KvModal>}
    {dialog === 'recall' && data && <KvModal title={`요청 취소, ${code}`} onClose={() => !busy && setDialog(null)}>
      <form onSubmit={submitRecall}>
        <div className="kv-sub" style={{ margin: '0 0 8px' }}>부서에 보낸 검토 요청을 회수하고 검토중 상태로 돌아갑니다.</div>
        <div className="kv-field"><span className="req">취소 사유</span><textarea name="reason" aria-label="취소 사유" rows={3} required /></div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(null)}>닫기</button><button type="submit" className="kb primary" disabled={busy}>요청 취소</button></div>
      </form>
    </KvModal>}
    {dialog === 'dept' && data && pendingResult && <KvModal title={`부서 답변 확인, ${code}`} onClose={() => !busy && setDialog(null)}>
      <DeptResultReview key={pendingResult.id} result={pendingResult} round={roundOf(pendingResult.transfer_id)} busy={busy} blocked={canWrite ? undefined : MOCK_BLOCKED}
        onSubmit={form => void run(form.decision === 'ACCEPT' ? '부서 답변을 확인했습니다.' : '같은 부서에 다시 요청했습니다.', signal => reviewDeptResult(ticketId, pendingResult.id, { ...form, version }, signal))} />
    </KvModal>}
    {dialog === 'resolution' && data && <KvModal title={`해결 기록 남기기, ${code}`} onClose={() => !busy && setDialog(null)}>
      <ResolutionCaseForm workflow={wf} busy={busy} onCancel={() => setDialog(null)} prefill={resolutionPrefill} suggest={canWrite ? signal => suggestFault(ticketId, signal) : undefined} faults={{ items: faultList.data?.data.items, pending: faultList.isLoading, failed: faultList.isError }}
        onSubmit={form => void run('해결 기록을 남겼습니다.', signal => registerResolutionCase(ticketId, { ...form, version }, signal))} />
    </KvModal>}
    {dialog === 'correct' && data && analysisData && <KvModal title={`분류 정정, ${code}`} onClose={() => setDialog(null)}>
      <AnalysisCorrection analysis={analysisData} onDone={() => setDialog(null)} onSubmit={async body => { try { await correctAnalysis(ticketId, body, new AbortController().signal); setNotice({ kind: 'ok', text: '분류 정정을 저장했습니다.' }); } finally { await Promise.allSettled([analysis.refetch(), audit.refetch()]); } }} />
    </KvModal>}
    {dialog === 'return' && data && <KvModal title={`반품 확정, ${code}`} onClose={() => !busy && setDialog(null)}>
      <TicketAnalysisPanel key={`${ticketId}:${returnFormKey}`} analysis={analysisData} pending={analysis.isPending} failed={analysis.isError} workflow={wf} ticketState={data.workflow_state} canWrite={canWrite} busy={busy}
        onConfirm={form => void run('반품 사유를 확정했습니다.', signal => confirmReturn(ticketId, { ...form, version }, signal)).then(ok => { if (ok) setReturnFormKey(key => key + 1); })} />
    </KvModal>}
    {dialog === 'draft' && data && <KvModal title={`답변·정보 요청 문안, ${code}`} onClose={() => !busy && setDialog(null)}>
      <AiDraftDialog analysis={analysisData} ticketLabel={`${data.customer_external_id}, ${data.body_language}${isMail ? '' : ` · ${channelName} 문의: 메일을 보내지 않고 문안을 복사해 직접 응대`}`} send={isMail ? sendOptions : undefined} initialPick={draftPick ?? chosen?.label ?? 'OWN'}
        onUse={(label, text) => { const option = draftOptions.find(item => item.label === label); setBoxPick(option ? label : 'OWN'); setEdited(option && textOf(option) === text ? null : text); setEditing(false); resetSend(); setCopyNote(''); setDialog(null); setNotice({ kind: 'ok', text: `${option ? OPTION_LABEL[label] ?? label : '직접 쓴'} 초안을 회신 상자에 넣었습니다.` }); }} />
    </KvModal>}
    {dialog === 'owner' && data && <KvModal title={`담당 변경, ${code}`} onClose={() => !busy && setDialog(null)}>
      <form onSubmit={event => { event.preventDefault(); const next = String(new FormData(event.currentTarget).get('owner') ?? ''); if (next) void run('담당자를 바꿨습니다.', signal => reassignOwner(ticketId, { owner_user_id: next, version }, signal)); }}>
        <div className="kv-field"><span>현재 담당</span><span className="ro">{owner}</span></div>
        <div className="kv-field"><span className="req">새 담당</span>
          {assignees.isPending ? <span className="ro">담당자 목록을 읽는 중입니다</span> : assignees.isError ? <span className="ro">담당자 목록을 불러오지 못했습니다</span>
            : <select name="owner" aria-label="새 담당" required defaultValue=""><option value="" disabled>선택</option>{assignees.data.data.items.filter(item => item.user_id !== wf?.owner_user_id).map(item => <option key={item.user_id} value={item.user_id}>{item.display_name}</option>)}</select>}</div>
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(null)}>취소</button><button type="submit" className="kb primary" disabled={busy || !assignees.isSuccess}>변경</button></div>
      </form>
    </KvModal>}
    {dialog === 'close' && data && <KvModal title={`종결, ${code}`} onClose={() => !busy && setDialog(null)}>
      <form onSubmit={event => { event.preventDefault(); const note = String(new FormData(event.currentTarget).get('note') ?? '').trim(); if (!isMail && noteRequired && !note) { setCloseNoteError(true); return; } setCloseNoteError(false); void run('문의를 종결했습니다.', signal => closeTicket(ticketId, version, signal, !isMail && note ? note : undefined)); }}>
        <div className="kv-sub" style={{ margin: '0 0 8px' }}>이 문의를 종결합니다. 종결 뒤에는 해결 기록을 한 건 남길 수 있습니다. 종결 조건은 서버가 확인합니다.</div>
        {!isMail && <div className="kv-field"><span className={noteRequired ? 'req' : undefined}>응대 내용</span>
          <div><textarea name="note" aria-label="응대 내용" rows={3} maxLength={2000} required={noteRequired} placeholder={`${channelName}로 고객에게 어떻게 안내했는지 적으세요. 예: 납기 4주와 인코텀즈 확인 요청을 전화로 안내함`} onChange={() => setCloseNoteError(false)} />
          {closeNoteError && <small className="nb-warn" role="alert" style={{ display: 'block', color: '#a3221a', marginTop: 3 }}>응대 내용을 적어 주세요. 공백만으로는 종결할 수 없습니다.</small>}
          <small className="filled">{channelName} 문의는 시스템에서 메일을 보내지 않아, 응대 내용이 종결 기록에 남습니다.</small></div></div>}
        <div className="kv-actions"><button type="button" className="kb" disabled={busy} onClick={() => setDialog(null)}>닫기</button><button type="submit" className="kb primary" disabled={busy}>종결</button></div>
      </form>
    </KvModal>}
  </main>;
}
