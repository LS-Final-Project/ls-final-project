import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getApiMode } from '../api/client';
import {
  DEPARTMENT_CODES, acknowledgeRequest, completeImprovement, planFollowup, recordDeptAction, replyToRequest, resendToDepartment, reviewImprovement, reviewRejection, sendImprovement,
  type DepartmentCode, type ImprovementDetail,
} from '../api/cs-improvements';
import { me } from '../api/platform';
import { DEPT_LABEL, REQUEST_STATUS_LABEL } from '../ls-shell/cs-labels';

type Detail = ImprovementDetail['data'];
type Department = Detail['departments'][number];
const SCOPE_KIND = { OTHER: '기타', FIRMWARE_VERSION: '펌웨어 버전', SHIPPED_FROM: '출하 시점부터', LOT: '로트' } as const;
const REPLY_TYPE = { ADOPTED: '채택', INFO_REQUESTED: '추가 정보 요청', REJECTED: '반려' } as const;
const today = () => new Date().toISOString().slice(0, 10);

/**
 * 개선요청의 상태 전이 입력(KAN-116): 검토 완료, 부서 전달, 부서 수신 확인·회신·조치, 다시 요청, 반려 확인, 후속 확인 계획, 완료.
 * 요청 상태는 사람이 고르지 않고 서버가 현재 회차 부서 상태에서 요약한다. 모든 호출은 읽은 판본을 보내고, 판본이 바뀌었으면 서버가 409로 거부한다.
 * 결과를 알 수 없으면(응답 없음·5xx) 같은 요청을 다시 보내지 않고 최신 내용을 읽는다.
 */
export type TransitionMessage = { kind: 'ok' | 'error'; text: string } | null;

// message는 상위가 들고 있다. 판본이 바뀌어 다시 읽는 동안 상위가 이 화면을 내려도 오류 안내('반영 여부를 확인하세요')가 사라지면 안 된다.
export default function ImprovementTransitions({ detail, onChanged, message, setMessage }: {
  detail: Detail; onChanged: (next?: ImprovementDetail) => void; message: TransitionMessage; setMessage: (next: TransitionMessage) => void;
}) {
  const request = detail.request;
  const version = request.version;
  const available = import.meta.env.DEV && getApiMode() === 'live';
  const identity = useQuery({ queryKey: ['improvement-identity', getApiMode()], queryFn: me, retry: false, enabled: available });
  const roles = identity.data?.data.roles ?? [];
  const ownDept = identity.data?.data.dept;
  const isCs = roles.includes('cs_agent');
  const isDept = roles.includes('dept_staff');
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  // 판본이 바뀌면 열어 둔 입력을 닫는다(앞 판본으로 쓴 내용이 남지 않게). 안내 문구는 지우지 않는다.
  useEffect(() => { setOpen(null); }, [request.id, version]);

  async function run(done: string, call: (signal: AbortSignal) => Promise<ImprovementDetail>) {
    if (controller.current) return;
    const current = new AbortController();
    controller.current = current;
    setBusy(true); setMessage(null);
    try {
      const result = await call(current.signal);
      if (current.signal.aborted) return;
      setMessage({ kind: 'ok', text: done }); setOpen(null);
      onChanged(result);
    } catch (caught) {
      if (current.signal.aborted) return;
      const status = caught instanceof ApiError ? caught.status : 0;
      const code = caught instanceof ApiError ? caught.code : '';
      setMessage({ kind: 'error', text: code === 'CS_REQUEST_VERSION_CONFLICT' ? '다른 사용자가 먼저 바꿨습니다. 최신 내용을 다시 읽었으니 확인한 뒤 다시 실행하세요.'
        : status >= 400 && status < 500 && status !== 408 ? (caught as ApiError).message
          : '결과를 확인하지 못했습니다. 같은 요청을 다시 보내지 않고 최신 내용을 읽었습니다. 반영 여부를 확인하세요.' });
      onChanged();
    } finally {
      if (controller.current === current) controller.current = null;
      if (!current.signal.aborted) setBusy(false);
    }
  }

  const closed = request.status === 'COMPLETED';
  const disabled = !available || busy || closed;
  const Open = ({ id, children }: { id: string; children: ReactNode }) => <button type="button" disabled={disabled} onClick={() => { setOpen(id); setMessage(null); }}>{children}</button>;
  const Cancel = () => <button type="button" disabled={busy} onClick={() => setOpen(null)}>취소</button>;
  const rows = detail.departments;
  const live = request.status !== 'FOLLOW_UP' && request.status !== 'COMPLETED';

  return <section aria-label="검토·전달·조치 입력">
    <h2>검토·전달·조치 입력</h2>
    <p>현재 {REQUEST_STATUS_LABEL[request.status] ?? request.status} · 판본 {version}. 상태는 부서 상태에서 서버가 정합니다. 문의 종결, 부서 조치, 고객 적용·재발 확인은 서로 다른 기록입니다.</p>
    {!available && <p>{getApiMode() === 'mock' ? '모의 응답에서는 실행하지 않습니다.' : '실제 API 개발 모드에서만 실행할 수 있습니다.'}</p>}
    {closed && <p>완료된 요청은 고칠 수 없습니다.</p>}
    {message?.kind === 'ok' && <p role="status">{message.text}</p>}
    {message?.kind === 'error' && <p role="alert">{message.text}</p>}

    {isCs && request.status === 'DRAFT' && <div>
      <Open id="review">검토 완료</Open>
      {open === 'review' && <DeptPickForm label="검토 완료" initial={request.proposed_depts as DepartmentCode[]} busy={busy} requireOne={false} cancel={<Cancel />}
        extra={<p>검토를 완료하면 작성 내용을 확정합니다. 제안 부서를 바꿀 수 있습니다.</p>}
        onSubmit={({ depts }) => void run('검토를 완료했습니다.', signal => reviewImprovement(request.id, { version, ...(sameSet(depts, request.proposed_depts) ? {} : { proposed_depts: depts }) }, signal))} />}
    </div>}

    {isCs && request.status === 'REVIEWED' && <div>
      <Open id="send">부서에 전달</Open>
      {open === 'send' && <DeptPickForm label="전달" initial={request.proposed_depts as DepartmentCode[]} busy={busy} requireOne cancel={<Cancel />} withDue
        extra={<p>{request.receiver_unconfirmed ? '받는 부서가 확인되지 않은 문서 요청입니다. 받는 부서를 골라야 전달할 수 있습니다. ' : ''}전달은 요청을 기록할 뿐 부서에 메일을 보내지 않습니다.</p>}
        onSubmit={({ depts, due }) => void run('부서에 전달했습니다.', signal => sendImprovement(request.id, { version, dept_codes: depts, reply_due_days: due }, signal))} />}
    </div>}

    {rows.length > 0 && <div>
      <h3>부서별 진행</h3>
      <ul>
        {rows.map(row => <DepartmentRow key={`${row.round}-${row.dept_code}`} row={row} canCs={isCs && live} canDept={isDept && ownDept === row.dept_code && live} open={open} setOpen={setOpen} disabled={disabled} busy={busy}
          onAck={() => void run('수신을 확인했습니다.', signal => acknowledgeRequest(request.id, row.dept_code, version, signal))}
          onRejectionReviewed={() => void run('반려를 확인했습니다.', signal => reviewRejection(request.id, row.dept_code, version, signal))}
          onResend={(reason, due) => void run('같은 부서에 다시 요청했습니다.', signal => resendToDepartment(request.id, row.dept_code, { version, reason, reply_due_days: due }, signal))}
          onReply={(type, reason) => void run('회신을 남겼습니다.', signal => replyToRequest(request.id, row.dept_code, { version, reply_type: type, reason: reason || null }, signal))}
          onAction={body => void run('조치를 기록했습니다.', signal => recordDeptAction(request.id, row.dept_code, { version, ...body }, signal))} />)}
      </ul>
    </div>}

    {isCs && request.status === 'ACTION_TAKEN' && <div>
      <Open id="plan">후속 확인 계획</Open>
      {open === 'plan' && <TextForm label="후속 확인 계획" field="후속 확인 계획" busy={busy} cancel={<Cancel />}
        note="모든 부서가 조치했거나 반려를 확인했고 조치가 1개 이상일 때만 넘어갑니다. 아니면 서버가 이유를 알려 줍니다."
        onSubmit={text => void run('후속 확인 계획을 남겼습니다.', signal => planFollowup(request.id, { version, followup_plan: text }, signal))} />}
    </div>}
    {isCs && request.status === 'FOLLOW_UP' && <div>
      <Open id="complete">완료 처리</Open>
      {open === 'complete' && <TextForm label="완료 처리" field="후속 확인 결과" busy={busy} cancel={<Cancel />}
        note="부서 회신과 조치만으로는 완료되지 않습니다. 후속 확인 결과를 적어야 완료합니다."
        onSubmit={text => void run('완료 처리했습니다.', signal => completeImprovement(request.id, { version, followup_result: text }, signal))} />}
    </div>}
    {!isCs && !isDept && available && identity.isSuccess && <p>이 세션은 C/S 담당자나 부서 담당자가 아니라 실행할 수 없습니다.</p>}
  </section>;
}

// 다시 요청은 추가 정보 요청·반려 뒤에, 또는 회신 기한이 지난 미회신 부서를 재촉할 때만 서버가 받는다(contracts/cs/improvements.md).
const canResend = (row: Department) => row.status === 'INFO_REQUESTED' || row.status === 'REJECTED'
  || ((row.status === 'SENT' || row.status === 'ACKNOWLEDGED') && !!row.reply_due_at && new Date(row.reply_due_at).getTime() < Date.now());
const sameSet = (left: readonly string[], right: readonly string[]) => left.length === right.length && left.every(item => right.includes(item));

function DepartmentRow({ row, canCs, canDept, open, setOpen, disabled, busy, onAck, onRejectionReviewed, onResend, onReply, onAction }: {
  row: Department; canCs: boolean; canDept: boolean; open: string | null; setOpen: (id: string | null) => void; disabled: boolean; busy: boolean;
  onAck: () => void; onRejectionReviewed: () => void; onResend: (reason: string, due: number) => void;
  onReply: (type: keyof typeof REPLY_TYPE, reason: string) => void;
  onAction: (body: { action: string; action_scope: string; action_date: string; action_evidence: string; scope_kind: keyof typeof SCOPE_KIND; scope_value: string | null }) => void;
}) {
  const key = (name: string) => `${name}:${row.dept_code}`;
  const Cancel = () => <button type="button" disabled={busy} onClick={() => setOpen(null)}>취소</button>;
  const toggle = (name: string, text: string) => <button type="button" disabled={disabled} onClick={() => setOpen(key(name))}>{text}</button>;
  const label = DEPT_LABEL[row.dept_code] ?? row.dept_code;
  return <li>
    <strong>{label} ({row.dept_code})</strong> · {REQUEST_STATUS_LABEL[row.status] ?? row.status}{row.carried_over ? ' · 이월' : ''}{row.reply_due_at ? ` · 회신 기한 ${row.reply_due_at.slice(0, 10)}` : ''}
    {row.status === 'REJECTED' && (row.rejection_reviewed_at ? ' · 반려 확인함' : ' · 반려 확인 전')}
    <div>
      {canDept && row.status === 'SENT' && <button type="button" disabled={disabled} onClick={onAck}>수신 확인</button>}
      {canDept && row.status === 'ACKNOWLEDGED' && toggle('reply', '회신')}
      {canDept && row.status === 'ADOPTED' && toggle('action', '조치 입력')}
      {canCs && row.status === 'REJECTED' && !row.rejection_reviewed_at && <button type="button" disabled={disabled} onClick={onRejectionReviewed}>반려 확인</button>}
      {canCs && canResend(row) && toggle('resend', '다시 요청')}
    </div>
    {open === key('resend') && <ResendForm busy={busy} cancel={<Cancel />} onSubmit={onResend} />}
    {open === key('reply') && <ReplyForm busy={busy} cancel={<Cancel />} onSubmit={onReply} />}
    {open === key('action') && <ActionForm busy={busy} cancel={<Cancel />} onSubmit={onAction} />}
  </li>;
}

function DeptPickForm({ label, initial, busy, requireOne, withDue, extra, cancel, onSubmit }: {
  label: string; initial: DepartmentCode[]; busy: boolean; requireOne: boolean; withDue?: boolean; extra?: ReactNode; cancel: ReactNode; onSubmit: (value: { depts: DepartmentCode[]; due: number }) => void;
}) {
  const [picked, setPicked] = useState<DepartmentCode[]>(initial);
  const [due, setDue] = useState(7);
  const valid = (!requireOne || picked.length > 0) && due >= 1 && due <= 90;
  return <form aria-label={`${label} 입력`} aria-busy={busy} onSubmit={(event: FormEvent) => { event.preventDefault(); if (valid && !busy) onSubmit({ depts: picked, due }); }}>
    {extra}
    <fieldset disabled={busy}>
      <legend>{label} 부서</legend>
      {DEPARTMENT_CODES.map(code => <label key={code}><input type="checkbox" checked={picked.includes(code)} onChange={event => setPicked(event.target.checked ? [...picked, code] : picked.filter(item => item !== code))} />{DEPT_LABEL[code] ?? code} ({code})</label>)}
    </fieldset>
    {withDue && <label>회신 기한(일)<input type="number" min={1} max={90} value={due} disabled={busy} onChange={event => setDue(Number(event.target.value))} /></label>}
    <button type="submit" disabled={busy || !valid}>{busy ? '처리 중' : label}</button>{cancel}
  </form>;
}

function TextForm({ label, field, note, busy, cancel, onSubmit }: { label: string; field: string; note: string; busy: boolean; cancel: ReactNode; onSubmit: (text: string) => void }) {
  const [text, setText] = useState('');
  return <form aria-label={`${label} 입력`} aria-busy={busy} onSubmit={(event: FormEvent) => { event.preventDefault(); if (text.trim() && !busy) onSubmit(text.trim()); }}>
    <p>{note}</p>
    <label>{field}<textarea required maxLength={10000} value={text} disabled={busy} onChange={event => setText(event.target.value)} /></label>
    <button type="submit" disabled={busy || !text.trim()}>{busy ? '처리 중' : label}</button>{cancel}
  </form>;
}

function ResendForm({ busy, cancel, onSubmit }: { busy: boolean; cancel: ReactNode; onSubmit: (reason: string, due: number) => void }) {
  const [reason, setReason] = useState('');
  const [due, setDue] = useState(7);
  return <form aria-label="다시 요청 입력" aria-busy={busy} onSubmit={(event: FormEvent) => { event.preventDefault(); if (reason.trim() && due >= 1 && due <= 90 && !busy) onSubmit(reason.trim(), due); }}>
    <p>추가 정보 요청에는 자료를 보완한 뒤, 반려에는 새 근거가 생긴 뒤에만 다시 요청할 수 있습니다. 회신 기한이 지나기 전의 재촉은 서버가 거부합니다.</p>
    <label>사유<textarea required value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
    <label>회신 기한(일)<input type="number" min={1} max={90} value={due} disabled={busy} onChange={event => setDue(Number(event.target.value))} /></label>
    <button type="submit" disabled={busy || !reason.trim()}>{busy ? '처리 중' : '다시 요청'}</button>{cancel}
  </form>;
}

function ReplyForm({ busy, cancel, onSubmit }: { busy: boolean; cancel: ReactNode; onSubmit: (type: keyof typeof REPLY_TYPE, reason: string) => void }) {
  const [type, setType] = useState<keyof typeof REPLY_TYPE>('ADOPTED');
  const [reason, setReason] = useState('');
  const needReason = type !== 'ADOPTED';
  return <form aria-label="회신 입력" aria-busy={busy} onSubmit={(event: FormEvent) => { event.preventDefault(); if (!busy && (!needReason || reason.trim())) onSubmit(type, reason.trim()); }}>
    <label>회신<select value={type} disabled={busy} onChange={event => setType(event.target.value as keyof typeof REPLY_TYPE)}>{(Object.keys(REPLY_TYPE) as (keyof typeof REPLY_TYPE)[]).map(item => <option key={item} value={item}>{REPLY_TYPE[item]}</option>)}</select></label>
    <label>{needReason ? '사유(필수)' : '메모(선택)'}<textarea value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
    <button type="submit" disabled={busy || (needReason && !reason.trim())}>{busy ? '처리 중' : '회신'}</button>{cancel}
  </form>;
}

function ActionForm({ busy, cancel, onSubmit }: { busy: boolean; cancel: ReactNode; onSubmit: Parameters<typeof DepartmentRow>[0]['onAction'] }) {
  const [form, setForm] = useState({ action: '', action_scope: '', action_evidence: '', action_date: today(), scope_kind: 'OTHER' as keyof typeof SCOPE_KIND, scope_value: '' });
  const set = (name: keyof typeof form, value: string) => setForm(current => ({ ...current, [name]: value }));
  const valid = form.action.trim() && form.action_scope.trim() && form.action_evidence.trim() && form.action_date;
  return <form aria-label="조치 입력" aria-busy={busy} onSubmit={(event: FormEvent) => {
    event.preventDefault();
    if (!valid || busy) return;
    onSubmit({ action: form.action.trim(), action_scope: form.action_scope.trim(), action_evidence: form.action_evidence.trim(), action_date: form.action_date, scope_kind: form.scope_kind, scope_value: form.scope_value.trim() || null });
  }}>
    <p>조치는 고객 한 명의 수리와 분리해 적용 범위와 실제 적용일, 근거를 남깁니다.</p>
    <label>조치 내용<textarea required value={form.action} disabled={busy} onChange={event => set('action', event.target.value)} /></label>
    <label>적용 대상<input required value={form.action_scope} disabled={busy} onChange={event => set('action_scope', event.target.value)} /></label>
    <label>적용 범위 구분<select value={form.scope_kind} disabled={busy} onChange={event => set('scope_kind', event.target.value)}>{(Object.keys(SCOPE_KIND) as (keyof typeof SCOPE_KIND)[]).map(item => <option key={item} value={item}>{SCOPE_KIND[item]}</option>)}</select></label>
    <label>적용 범위 값(선택)<input value={form.scope_value} disabled={busy} onChange={event => set('scope_value', event.target.value)} /></label>
    <label>실제 적용일<input type="date" required value={form.action_date} disabled={busy} onChange={event => set('action_date', event.target.value)} /></label>
    <label>근거<textarea required value={form.action_evidence} disabled={busy} onChange={event => set('action_evidence', event.target.value)} /></label>
    <button type="submit" disabled={busy || !valid}>{busy ? '처리 중' : '조치 기록'}</button>{cancel}
  </form>;
}
