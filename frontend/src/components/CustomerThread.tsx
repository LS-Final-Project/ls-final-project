import { formatStamp } from '../ls-shell/cs-labels';
import type { WorkflowRecord } from '../api/cs-workflow';

type Reply = NonNullable<WorkflowRecord['customer_replies']>[number];
type Send = NonNullable<WorkflowRecord['customer_sends']>[number];
type Original = { subject: string | null; body: string; received_at: string; error_code_reported: string | null; fw_version_reported: string | null };
type Message = { key: string; at: string; head: string; meta: string[]; text: string | null; subject?: string; plain?: boolean; note?: string; isNew?: boolean; send?: Send };
type Actions = { busy: boolean; refresh: (sendId: string) => void; retry: (sendId: string) => void };
const REQUEST_LABEL: Record<string, string> = { PENDING: '플랫폼 응답 전', REGISTERED: '플랫폼에 넘김', REJECTED: '플랫폼이 거부', REQUEST_FAILED: '넘기지 못함', REQUEST_UNKNOWN: '넘김 결과 모름' };

const KIND_LABEL: Record<string, string> = { ANSWER: '답변 발송', INFO_REQUEST: '정보 요청 발송' };
const SEND_STATUS_LABEL: Record<string, string> = { QUEUED: '대기', SUBMITTING: '보내는 중', ACCEPTED: '접수됨', UNKNOWN: '결과 모름', FAILED: '실패' };
const METHOD_LABEL: Record<string, string> = { HEADER: '답장 헤더로 연결', THREAD: '대화 묶음으로 연결', HUMAN: '사람이 연결' };

/**
 * 대화(최신이 위). 고객 원문, 우리가 보낸 답변·정보 요청, 고객 회신 도착을 시간순으로 보인다.
 * 고객 회신의 본문은 저장하지 않아 도착 사실만 보이고, 본문은 메일 계정에서 확인한다.
 * workflow가 없으면(권한 없음 등) 원문만 보인다.
 */
export default function CustomerThread({ original, workflow, actions }: { original: Original; workflow: WorkflowRecord | undefined; actions?: Actions }) {
  const replies = workflow?.customer_replies ?? [];
  const sends = workflow?.customer_sends ?? [];
  const lastSend = [...sends].map(item => item.accepted_at ?? item.approved_at).sort().at(-1);
  const messages: Message[] = [
    { key: 'original', at: original.received_at, head: '고객 원문', meta: [`보고된 오류 코드 ${original.error_code_reported ?? '-'}`, `보고된 FW ${original.fw_version_reported ?? '-'}`], text: original.body, subject: original.subject || '(제목 없음)', plain: true },
    ...sends.map((item: Send): Message => ({
      key: `send-${item.id}`, at: item.accepted_at ?? item.approved_at, head: KIND_LABEL[item.kind] ?? item.kind,
      meta: [`승인 ${item.approved_by.slice(0, 8)}`, REQUEST_LABEL[item.request_status] ?? item.request_status, SEND_STATUS_LABEL[item.send_status ?? ''] ?? '접수 전', ...(item.send_error_code ? [item.send_error_code] : [])], text: item.body, send: item,
    })),
    ...replies.map((item: Reply): Message => ({
      key: `reply-${item.message_id}`, at: item.received_at, head: '고객 회신',
      meta: [METHOD_LABEL[item.method] ?? item.method, ...(item.reopened ? ['검토중으로 복귀'] : [])], text: null,
      note: '회신 본문은 메일 계정에서 확인합니다. 여기에는 저장하지 않습니다.', isNew: !lastSend || item.received_at > lastSend,
    })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  return <div className="kv-thread" role="log" aria-label="대화">
    {messages.map(message => <div key={message.key} className={message.isNew ? 'msg new' : 'msg'}>
      <div className="hd"><b>{message.head}</b><span>{formatStamp(message.at)}</span>{message.meta.map(text => <span key={text}>{text}</span>)}</div>
      {message.subject && <div style={{ color: 'var(--erp-text)', marginBottom: 2 }}>{message.subject}</div>}
      {message.text === null ? <span className="unc">{message.note}</span> : message.plain ? <div className="kv-text en">{message.text}</div> : message.text}
      {actions && message.send && <SendActions send={message.send} actions={actions} />}
    </div>)}
  </div>;
}

/** 가장 늦게 도착한 고객 회신(우리 마지막 발송보다 뒤인 것만). 다음 할 일 줄에 쓴다. */
export function latestUnansweredReply(workflow: WorkflowRecord | undefined): Reply | undefined {
  if (!workflow) return undefined;
  const lastSend = [...(workflow.customer_sends ?? [])].map(item => item.accepted_at ?? item.approved_at).sort().at(-1);
  return [...(workflow.customer_replies ?? [])].sort((a, b) => b.received_at.localeCompare(a.received_at)).find(item => !lastSend || item.received_at > lastSend);
}

// 결과가 정해지지 않은 발송에만 단추를 둔다. 넘김 결과를 모르면(응답 없음) 새로 승인하지 않고 같은 내용을 다시 요청한다.
function SendActions({ send, actions }: { send: Send; actions: Actions }) {
  const open = send.request_status === 'PENDING' || send.request_status === 'REQUEST_FAILED' || send.request_status === 'REQUEST_UNKNOWN';
  const waiting = send.request_status === 'REGISTERED' && send.send_status !== 'ACCEPTED' && send.send_status !== 'FAILED';
  if (!open && !waiting) return null;
  return <div className="kv-actions" style={{ justifyContent: 'flex-start' }}>
    {waiting && <button type="button" className="kb" disabled={actions.busy} onClick={() => actions.refresh(send.id)}>상태 확인</button>}
    {open && <button type="button" className="kb" disabled={actions.busy} onClick={() => actions.retry(send.id)}>다시 요청</button>}
  </div>;
}
