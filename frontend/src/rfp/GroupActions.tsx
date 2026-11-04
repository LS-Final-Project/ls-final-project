import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';
import { ApiError, getApiMode } from '../api/client';
import { me } from '../api/platform';
import { createQuestionsBatch, listProjectCandidates, listQuestions, selectSupplementBatch, setRfpReviewDepartments, unsupportedRoute, type Question, type QuestionCreate, type RequirementDetail, type SupplementBatchResult } from '../api/rfp';
import { getSupplementOptions, getSupplementRequirement, type RequirementSummary, type SupplementOptions } from '../api/rfp-supplement';
import { getProject } from '../api/tft';
import { ErrorState, Loading } from '../components/states';
import { KvGrid, KvModal, KvRibbon, type KvColumn } from '../ls-shell/kv';
import { groupPartners } from './equipment-groups';
import { batchProblems, draftQuestion, mapLimited, groupSummary, isCustomerRequest, itemReason, outboxItem, outboxText, REQUEST_ISSUE, shortText, subjectOf, valueOf, type GroupAction, type RequestKind, type Situation } from './group-actions';
import { ReplyForm } from './QuestionForms';
import { DEPARTMENT_LABEL, PATH_LABEL, label, requirementNo } from './labels';
import { settled, useAttemptKey } from './proposal-shared';
import './GroupActions.css';

// 묶음 쓰기는 한 번에 50건까지다(서버 계약).
const LIMIT = 50;
const UNSUPPORTED = '이 서버는 아직 여러 건을 한 번에 기록하지 못합니다. 요구를 하나씩 열어 기록하세요.';

const useProject = (projectId: string) => useQuery({ queryKey: ['projects', getApiMode(), projectId], queryFn: () => getProject(projectId), retry: false });
const useQuestions = (projectId: string, actorId?: string) => useQuery({ queryKey: ['rfp-questions', getApiMode(), projectId, actorId], queryFn: () => listQuestions(projectId, undefined, { useMockFallback: false }), retry: false, refetchOnWindowFocus: false });
const currentDetail = (row: RequirementSummary, data?: { data: RequirementDetail }) => data?.data.requirement_id === row.requirement_id && data.data.version === row.version ? data.data : undefined;
// 고른 요구의 상세(원문 조항·인용·값). 판본이 다르거나 읽지 못한 건은 null이다.
function useDetails(actorId: string | undefined, projectId: string, rows: RequirementSummary[], enabled = true) {
  return useQuery({
    queryKey: ['rfp-batch-details', getApiMode(), actorId, projectId, rows.map(r => `${r.requirement_id}:${r.version}`)], enabled, retry: false, refetchOnWindowFocus: false,
    queryFn: () => mapLimited(rows, 4, row => getSupplementRequirement(row.requirement_id).then(data => currentDetail(row, data) ?? null, () => null)),
  });
}
const isOwner = (project: ReturnType<typeof useProject>, actorId?: string) => !!actorId && project.isSuccess && [project.data.data.sales_user_id, project.data.data.responsible_user_id].includes(actorId);
const recipientOf = (q: Question) => q.target === 'CUSTOMER' ? '발주처' : label(DEPARTMENT_LABEL, q.target_dept) || '내부 부서';

// 기록 뒤에 요구 목록·질문·카드가 새 상태를 읽게 한다.
function refreshAfterWrite(client: QueryClient) {
  for (const key of ['rfp-requirements', 'rfp-review-project-requirements', 'rfp-questions', 'rfp-guided-questions', 'rfp-development-questions', 'rfp-response-detail', 'rfp-response']) void client.invalidateQueries({ queryKey: [key] });
}
async function sameActor(actorId: string) {
  const actor = await me();
  if (!actor || actor.data.id !== actorId) throw new ApiError(409, '세션 사용자가 변경되었습니다. 다시 확인해 주세요.', undefined, 'SESSION_CHANGED');
}
function downloadText(name: string, text: string) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([`﻿${text.replace(/\n/g, '\r\n')}`], { type: 'text/plain;charset=utf-8' }));
  link.download = `${name.replace(/[\\/:*?"<>|]+/g, '_')}.txt`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href));
}

type Dialog = { type: 'request'; kind: RequestKind; rows: RequirementSummary[] } | { type: 'selection'; rows: RequirementSummary[]; task: boolean } | { type: 'outbox' } | { type: 'replies' };

export function GroupSummary({ projectId, actorId, name, rows, busy = false, collapsed = false, onShowRows }: {
  projectId: string; actorId?: string; name: string; rows: RequirementSummary[]; busy?: boolean; collapsed?: boolean; onShowRows: (situation: Situation) => void;
}) {
  const mode = getApiMode();
  const project = useProject(projectId);
  const questions = useQuestions(projectId, actorId);
  const candidates = useQuery({ queryKey: ['rfp-group-candidates', mode, projectId, actorId], queryFn: ({ signal }) => listProjectCandidates(projectId, signal), retry: false, refetchOnWindowFocus: false });
  const [dialog, setDialog] = useState<Dialog>();
  const summary = groupSummary(rows, questions.isSuccess ? questions.data.data.items : []);
  const canWrite = isOwner(project, actorId) && !busy;
  const partners = candidates.isSuccess ? groupPartners({ rows }, candidates.data.data.items).filter(p => p.grades.some(g => g.grade !== 'EXCLUDED')).length : undefined;
  const count = (action: GroupAction) => action.questions?.length ?? action.rows.length;
  const open = (action: GroupAction): { label: string; act: () => void; write: boolean; secondary: { label: string; act: () => void; write: boolean } } => {
    const list = { label: '목록에서 보기', act: () => onShowRows(action.key as Situation), write: false };
    switch (action.key) {
      case 'material': return { label: action.next, act: () => setDialog({ type: 'request', kind: 'material', rows: action.rows }), write: true, secondary: list };
      case 'customer': return { label: action.next, act: () => setDialog({ type: 'request', kind: 'customer-value', rows: action.rows }), write: true, secondary: list };
      case 'task': return { label: action.next, act: () => setDialog({ type: 'selection', rows: action.rows, task: true }), write: true, secondary: list };
      case 'gap': return { label: action.next, act: list.act, write: false, secondary: { label: '같은 방안으로 기록', act: () => setDialog({ type: 'selection', rows: action.rows, task: false }), write: true } };
      case 'conditional': return { label: action.next, act: list.act, write: false, secondary: { label: '확인 요청서 만들기', act: () => setDialog({ type: 'request', kind: 'condition', rows: action.rows }), write: true } };
      case 'replies': return { label: action.next, act: () => setDialog({ type: 'replies' }), write: true, secondary: { label: '보낼 내용 보기', act: () => setDialog({ type: 'outbox' }), write: false } };
      default: return { label: action.next, act: list.act, write: false, secondary: { label: '', act: () => {}, write: false } };
    }
  };
  const close = () => setDialog(undefined);
  // 대화상자는 접힘 상태와 따로 둔다(details가 다시 그려져도 입력이 남는다).
  return <><details key={String(collapsed)} className="rfp-group-summary" open={!collapsed}>
    <summary><b>이 묶음에서 할 일</b><span>{questions.isPending ? '확인 중' : summary.actions.length ? `${summary.actions.length}가지` : '남은 일 없음'}</span></summary>
    <KvRibbon columns={3} cells={[
      ['근거 있음', `${summary.counts.evidence}건`], ['남은 조건', `${summary.counts.conditional}건`], ['사양 차이', `${summary.counts.gap}건`],
      ['자료 부족', `${summary.counts.material}건`], ['과업', `${summary.counts.task}건`], ['협력사 후보', candidates.isError ? '조회 실패' : partners === undefined ? '확인 중' : `${partners}곳`],
    ]} />
    {questions.isError && <p role="alert" className="rfp-group-note">질문 기록을 읽지 못해 회신 대기 건수를 셀 수 없습니다. <button type="button" className="kb" onClick={() => questions.refetch()}>다시 읽기</button></p>}
    {summary.actions.length > 0 ? <ol className="rfp-group-actions">{summary.actions.map(action => {
      const entry = open(action);
      return <li key={action.key}>
        <button type="button" className="rfp-group-action" disabled={entry.write && !canWrite} aria-haspopup={entry.write ? 'dialog' : undefined} onClick={entry.act}><b>{action.title} {count(action)}건</b><span>{entry.label}</span></button>
        {entry.secondary.label && <button type="button" className="kb" disabled={entry.secondary.write && !canWrite} onClick={entry.secondary.act}>{entry.secondary.label}</button>}
      </li>;
    })}</ol> : !questions.isPending && <p className="rfp-group-note">남은 할 일이 없습니다. 기록된 대응은 요구별로 확인하세요.</p>}
    {project.isSuccess && !canWrite && <p className="rfp-group-note">질문과 대응 기록은 프로젝트 담당자·책임자만 만들 수 있습니다.</p>}
  </details>
    {dialog?.type === 'request' && actorId && <RequestDialog projectId={projectId} actorId={actorId} kind={dialog.kind} rows={dialog.rows} groupName={name} onClose={close} />}
    {dialog?.type === 'selection' && actorId && <SelectionDialog projectId={projectId} actorId={actorId} rows={dialog.rows} task={dialog.task} groupName={name} onClose={close} />}
    {dialog?.type === 'outbox' && <OutboxDialog projectId={projectId} actorId={actorId} rows={rows} groupName={name} onClose={close} />}
    {dialog?.type === 'replies' && <RepliesDialog projectId={projectId} actorId={actorId} rows={rows} groupName={name} onClose={close} onOutbox={() => setDialog({ type: 'outbox' })} />}
  </>;
}

// 받는 부서. 등록된 부서가 없으면 이 자리에서 등록한다(등록하면 그 부서 담당자도 프로젝트를 볼 수 있다).
export function DepartmentPicker({ projectId, actorId, value, onChange }: { projectId: string; actorId: string; value: string; onChange: (code: string) => void }) {
  const mode = getApiMode(), client = useQueryClient();
  const project = useProject(projectId);
  const data = project.isSuccess ? project.data.data : undefined;
  const registered = data?.review_departments ?? [];
  const [adding, setAdding] = useState(false);
  const [code, setCode] = useState('');
  const [agreed, setAgreed] = useState(false);
  const canEdit = isOwner(project, actorId);
  const unregistered = Object.keys(DEPARTMENT_LABEL).filter(c => !registered.includes(c));
  const save = useMutation({
    mutationFn: async () => {
      if (!data || !code) throw new ApiError(403, '프로젝트 권한과 판본을 다시 확인하세요.');
      const result = await setRfpReviewDepartments(projectId, data.version, [...registered, code], actorId);
      if (result.data.id !== projectId || !result.data.review_departments.includes(code)) throw new ApiError(409, '저장 응답을 확인할 수 없습니다. 프로젝트를 새로 조회하세요.');
      return result;
    }, retry: false,
    onSuccess: result => { client.setQueryData(['projects', mode, projectId], result); void client.invalidateQueries({ queryKey: ['projects', mode, projectId] }); setAdding(false); setAgreed(false); onChange(code); },
  });
  if (project.isPending) return <Loading label="검토 부서 확인 중" />;
  if (project.isError) return <ErrorState error={project.error} onRetry={() => project.refetch()} />;
  const showAdd = adding || !registered.length;
  return <div className="rfp-dept-picker">
    {registered.length > 0 && <label>받는 부서<select aria-label="받는 부서" value={value} onChange={e => e.target.value === 'add' ? setAdding(true) : onChange(e.target.value)}>
      <option value="">부서 선택</option>
      {registered.map(c => <option key={c} value={c}>{label(DEPARTMENT_LABEL, c)}</option>)}
      {canEdit && unregistered.length > 0 && <option value="add">다른 부서 등록</option>}
    </select></label>}
    {showAdd && (canEdit ? <fieldset className="rfp-dept-add">
      <legend>{registered.length ? '받을 부서 등록' : '이 프로젝트에 등록된 검토 부서가 없습니다. 받을 부서를 등록하세요.'}</legend>
      <select aria-label="등록할 부서" value={code} onChange={e => setCode(e.target.value)}><option value="">부서 선택</option>{unregistered.map(c => <option key={c} value={c}>{DEPARTMENT_LABEL[c]}</option>)}</select>
      <label className="rfp-dept-consent"><input type="checkbox" checked={agreed} onChange={e => setAgreed(e.target.checked)} />이 부서 담당자도 이 프로젝트와 RFP 기록을 볼 수 있게 됩니다.</label>
      <div className="rfp-dialog-actions">{registered.length > 0 && <button type="button" className="kb" onClick={() => setAdding(false)}>취소</button>}<button type="button" className="kb primary" disabled={!code || !agreed || save.isPending} onClick={() => save.mutate()}>{save.isPending ? '등록 중' : '부서 등록'}</button></div>
      {save.isError && <ErrorState error={save.error} />}
    </fieldset> : <p className="rfp-group-note">검토 부서 등록은 프로젝트 담당자·책임자가 합니다.</p>)}
  </div>;
}

const REQUEST_TITLE: Record<RequestKind, string> = {
  material: '자료 요청서 만들기', condition: '적용 조건 확인 요청서 만들기', 'customer-value': '발주처 질의 목록 만들기', 'customer-scope': '발주처 질의 목록 만들기', 'customer-condition': '발주처 질의 목록 만들기',
};
function Steps({ steps, current }: { steps: string[]; current: number }) {
  return <ol className="rfp-dialog-steps">{steps.map((step, index) => <li key={step} aria-current={index === current ? 'step' : undefined}>{step}</li>)}</ol>;
}
function PickRows({ rows, checked, onChange }: { rows: RequirementSummary[]; checked: Set<string>; onChange: (next: Set<string>) => void }) {
  const toggle = (id: string, on: boolean) => { const next = new Set(checked); if (on) next.add(id); else next.delete(id); onChange(next); };
  const columns: KvColumn<RequirementSummary>[] = [
    { key: 'pick', header: '선택', width: 44, className: 'c', cell: row => <input type="checkbox" aria-label={`요구 ${requirementNo(row.code)} 선택`} checked={checked.has(row.requirement_id)} disabled={!checked.has(row.requirement_id) && checked.size >= LIMIT} onChange={e => toggle(row.requirement_id, e.target.checked)} /> },
    { key: 'no', header: '요구', width: 110, cell: row => requirementNo(row.code) },
    { key: 'subject', header: '내용', cell: row => subjectOf(row) },
    { key: 'value', header: '고객 요구', width: 200, cell: row => shortText(row.value_text ?? '', 60) || '값 없음' },
  ];
  return <>
    <div className="rfp-dialog-bar"><span>{checked.size}건 선택 · 한 번에 {LIMIT}건까지</span><span><button type="button" className="kb" onClick={() => onChange(new Set(rows.slice(0, LIMIT).map(r => r.requirement_id)))}>처음 {Math.min(LIMIT, rows.length)}건 고르기</button><button type="button" className="kb" onClick={() => onChange(new Set())}>모두 해제</button></span></div>
    <KvGrid label="요구 선택" columns={columns} rows={rows} rowKey={row => row.requirement_id} fallback="고를 요구가 없습니다." scroll />
    {rows.length > LIMIT && <p className="rfp-group-note">나머지 {rows.length - Math.min(LIMIT, checked.size)}건은 이번 기록 뒤에 다시 고르세요.</p>}
  </>;
}

// 요구 여러 건에 질문을 만든다: 대상 고르기 → 문안 확인 → 보낼 내용. 저장은 앱 기록이고 메일을 보내지 않는다.
export function RequestDialog({ projectId, actorId, kind, rows, groupName, onClose }: { projectId: string; actorId: string; kind: RequestKind; rows: RequirementSummary[]; groupName: string; onClose: () => void }) {
  const client = useQueryClient();
  const project = useProject(projectId);
  const customer = isCustomerRequest(kind);
  const multi = rows.length > 1;
  const [step, setStep] = useState<'pick' | 'review' | 'done'>(multi ? 'pick' : 'review');
  const [checked, setChecked] = useState(() => new Set(rows.slice(0, LIMIT).map(r => r.requirement_id)));
  const [dept, setDept] = useState('');
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [created, setCreated] = useState<Question[]>([]);
  const picked = rows.filter(r => checked.has(r.requirement_id));
  const details = useDetails(actorId, projectId, picked, step !== 'pick');
  const items = picked.map((row, index) => {
    const detail = details.data?.[index] ?? undefined;
    return { row, detail, pending: details.isPending, failed: details.isSuccess && !detail, text: texts[row.requirement_id] ?? (detail ? draftQuestion(kind, detail) : '') };
  });
  const ready = items.filter(item => item.detail && item.text.trim());
  const body: QuestionCreate[] = ready.map(item => ({
    requirement_id: item.row.requirement_id, origin: { clause_id: item.detail!.origin.clause_id, page: item.detail!.origin.page },
    issue_type: REQUEST_ISSUE[kind], question_text: item.text.trim(), target: customer ? 'CUSTOMER' : 'INTERNAL', target_dept: customer ? null : dept, internal_assumption: null,
  }));
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const create = useMutation({
    mutationFn: async () => { await sameActor(actorId); return createQuestionsBatch(projectId, body, key); }, retry: false,
    // 요구 목록은 대화상자를 닫을 때 새로 읽는다. 지금 읽으면 목록이 다시 그려지며 보낼 내용이 사라질 수 있다.
    onSuccess: result => { nextKey(); setCreated(result.data.items); setStep('done'); void client.invalidateQueries({ queryKey: ['rfp-questions'] }); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  const problems = create.isError ? batchProblems(create.error) : new Map<number, string>();
  // 거절되면 첫 문제 항목으로 초점을 옮긴다.
  useEffect(() => { if (problems.size) document.querySelector<HTMLElement>('.rfp-dialog-items .has-problem textarea')?.focus(); }, [create.error]);
  const problemOf = (id: string) => { const index = ready.findIndex(item => item.row.requirement_id === id); return index < 0 ? undefined : problems.get(index); };
  const canWrite = isOwner(project, actorId);
  const loading = items.some(item => item.pending);
  const canCreate = canWrite && !loading && ready.length > 0 && (customer || !!dept) && !create.isPending;
  const steps = multi ? ['대상 고르기', '문안 확인', '보낼 내용'] : ['문안 확인', '보낼 내용'];
  const stepIndex = { pick: 0, review: multi ? 1 : 0, done: multi ? 2 : 1 }[step];
  const close = () => { if (created.length) refreshAfterWrite(client); onClose(); };
  return <KvModal title={`${REQUEST_TITLE[kind]} · ${groupName}`} onClose={close} className="rfp-dialog" keepOnBackdrop>
    <Steps steps={steps} current={stepIndex} />
    {step === 'pick' && <>
      <p className="rfp-dialog-lead">{customer ? '고른 요구마다 발주처 질의를 하나씩 기록합니다.' : '고른 요구마다 기술 자문 질문을 하나씩 기록합니다.'} 다음 단계에서 문안을 고칠 수 있습니다.</p>
      <PickRows rows={rows} checked={checked} onChange={setChecked} />
      <div className="rfp-dialog-actions"><button type="button" className="kb" onClick={onClose}>취소</button><button type="button" className="kb primary" disabled={!checked.size} onClick={() => setStep('review')}>문안 확인</button></div>
    </>}
    {step === 'review' && <>
      {customer ? <p className="rfp-dialog-lead">받는 곳: 발주처</p> : <DepartmentPicker projectId={projectId} actorId={actorId} value={dept} onChange={setDept} />}
      <div className="rfp-dialog-items">{items.map(item => {
        const problem = problemOf(item.row.requirement_id);
        return <article key={item.row.requirement_id} className={problem ? 'has-problem' : undefined}>
          <header><b>요구 {requirementNo(item.row.code)}</b><span>{subjectOf(item.row)}</span></header>
          {valueOf(item.row, item.detail) && <p className="rfp-dialog-value">고객 요구: {valueOf(item.row, item.detail)}</p>}
          {item.detail?.origin.quote && <q>{shortText(item.detail.origin.quote)}</q>}
          {item.pending ? <Loading label="문안 준비 중" /> : item.failed ? <p role="alert">요구가 바뀌었거나 읽지 못해 이번 기록에서 뺍니다. 목록을 새로 조회한 뒤 다시 만드세요.</p>
            : <textarea aria-label={`요구 ${requirementNo(item.row.code)} 질문`} rows={3} maxLength={4000} value={item.text} onChange={e => setTexts({ ...texts, [item.row.requirement_id]: e.target.value })} />}
          {problem && <p role="alert">{problem}</p>}
        </article>;
      })}</div>
      {create.isError && (unsupportedRoute(create.error) ? <p role="alert">{UNSUPPORTED}</p> : problems.size ? <p role="alert">{problems.size}건에 문제가 있어 아무것도 기록하지 않았습니다. 표시된 요구를 고치거나 선택에서 빼고 다시 기록하세요.</p> : <ErrorState error={create.error} />)}
      <p className="rfp-group-note">질문 {ready.length}건을 앱에 기록합니다. 메일은 보내지 않습니다.{!customer && !dept ? ' 받는 부서를 먼저 고르세요.' : ''}{!canWrite && project.isSuccess ? ' 프로젝트 담당자·책임자만 기록할 수 있습니다.' : ''}</p>
      <div className="rfp-dialog-actions">{multi ? <button type="button" className="kb" onClick={() => { create.reset(); setStep('pick'); }}>대상 다시 고르기</button> : <button type="button" className="kb" onClick={onClose}>취소</button>}<button type="button" className="kb primary" disabled={!canCreate} onClick={() => create.mutate()}>{create.isPending ? '기록 중' : `질문 ${ready.length}건 기록`}</button></div>
    </>}
    {step === 'done' && <>
      <p role="status" className="rfp-dialog-lead">질문 {created.length}건을 기록했습니다. 아래 내용을 복사해 직접 보내세요.</p>
      <Outbox projectName={project.data?.data.name ?? '프로젝트'} questions={created} rows={rows} details={new Map(items.flatMap(item => item.detail ? [[item.row.requirement_id, item.detail] as const] : []))} />
      <div className="rfp-dialog-actions"><button type="button" className="kb primary" onClick={close}>닫기</button></div>
    </>}
  </KvModal>;
}

// 받는 곳마다 보낼 글 하나. 복사와 .txt 내려받기만 한다.
export function Outbox({ projectName, questions, rows, details }: { projectName: string; questions: Question[]; rows: RequirementSummary[]; details: Map<string, RequirementDetail> }) {
  const [notice, setNotice] = useState('');
  const order = new Map(rows.map((row, index) => [row.requirement_id, index]));
  const groups = new Map<string, Question[]>();
  for (const q of [...questions].sort((a, b) => (order.get(a.requirement_id ?? '') ?? 0) - (order.get(b.requirement_id ?? '') ?? 0))) groups.set(recipientOf(q), [...groups.get(recipientOf(q)) ?? [], q]);
  const copy = async (recipient: string, text: string) => {
    try { await navigator.clipboard.writeText(text); setNotice(`${recipient}에 보낼 내용을 복사했습니다.`); } catch { setNotice('복사하지 못했습니다. 글을 직접 선택해 복사하세요.'); }
  };
  return <div className="rfp-outbox">
    {[...groups].map(([recipient, items]) => {
      const text = outboxText({ recipient, customer: items[0].target === 'CUSTOMER', projectName, items: items.map(q => outboxItem(q, rows.find(row => row.requirement_id === q.requirement_id), details.get(q.requirement_id ?? ''))) });
      return <section key={recipient} aria-label={`${recipient}에 보낼 내용`}>
        <header><b>{recipient} · {items.length}건</b><span><button type="button" className="kb" onClick={() => void copy(recipient, text)}>복사</button><button type="button" className="kb" onClick={() => downloadText(`${projectName}-${recipient}-${items[0].target === 'CUSTOMER' ? '질의' : '자료요청'}`, text)}>내려받기(.txt)</button></span></header>
        <textarea readOnly rows={Math.min(16, text.split('\n').length)} value={text} aria-label={`${recipient}에 보낼 글`} />
      </section>;
    })}
    {!groups.size && <p>보낼 질문이 없습니다.</p>}
    {notice && <p role="status">{notice}</p>}
    <p className="rfp-group-note">앱은 메일을 보내지 않습니다. 회신을 받으면 이 묶음의 '회신 기록 대기'에서 질문별로 기록하세요.</p>
  </div>;
}

function useOpenQuestions(projectId: string, actorId: string | undefined, rows: RequirementSummary[]) {
  const questions = useQuestions(projectId, actorId);
  const ids = new Set(rows.map(row => row.requirement_id));
  const open = questions.isSuccess ? questions.data.data.items.filter(q => q.status === 'OPEN' && !!q.requirement_id && ids.has(q.requirement_id)) : [];
  return { questions, open };
}

export function OutboxDialog({ projectId, actorId, rows, groupName, onClose }: { projectId: string; actorId?: string; rows: RequirementSummary[]; groupName: string; onClose: () => void }) {
  const project = useProject(projectId);
  const { questions, open } = useOpenQuestions(projectId, actorId, rows);
  const [recipient, setRecipient] = useState('');
  const recipients = [...new Set(open.map(recipientOf))];
  const shown = open.filter(q => !recipient || recipientOf(q) === recipient);
  const asked = rows.filter(row => shown.some(q => q.requirement_id === row.requirement_id));
  const details = useDetails(actorId, projectId, asked);
  const detailMap = new Map(asked.flatMap((row, index) => { const d = details.data?.[index]; return d ? [[row.requirement_id, d] as const] : []; }));
  return <KvModal title={`보낼 내용 · ${groupName}`} onClose={onClose} className="rfp-dialog" keepOnBackdrop>
    {questions.isPending ? <Loading label="질문 기록 읽는 중" /> : questions.isError ? <ErrorState error={questions.error} onRetry={() => questions.refetch()} /> : <>
      {recipients.length > 1 && <label className="rfp-dialog-filter">받는 곳<select aria-label="받는 곳" value={recipient} onChange={e => setRecipient(e.target.value)}><option value="">전체 {open.length}건</option>{recipients.map(r => <option key={r} value={r}>{r} {open.filter(q => recipientOf(q) === r).length}건</option>)}</select></label>}
      <p className="rfp-dialog-lead">회신을 기다리는 질문 {shown.length}건입니다.</p>
      {details.isFetching && <Loading label="원문 인용 읽는 중" />}
      <Outbox projectName={project.data?.data.name ?? '프로젝트'} questions={shown} rows={rows} details={detailMap} />
    </>}
    <div className="rfp-dialog-actions"><button type="button" className="kb primary" onClick={onClose}>닫기</button></div>
  </KvModal>;
}

export function RepliesDialog({ projectId, actorId, rows, groupName, onClose, onOutbox }: { projectId: string; actorId?: string; rows: RequirementSummary[]; groupName: string; onClose: () => void; onOutbox: () => void }) {
  const client = useQueryClient();
  const { questions, open } = useOpenQuestions(projectId, actorId, rows);
  const [picked, setPicked] = useState<string>();
  const [notice, setNotice] = useState('');
  const [replied, setReplied] = useState(false);
  const close = () => { if (replied) refreshAfterWrite(client); onClose(); };
  const question = open.find(q => q.question_id === picked);
  const rowOf = (q: Question) => rows.find(row => row.requirement_id === q.requirement_id);
  const columns: KvColumn<Question>[] = [
    { key: 'no', header: '요구', width: 100, cell: q => { const row = rowOf(q); return row ? requirementNo(row.code) : '번호 미확인'; } },
    { key: 'to', header: '받는 곳', width: 110, cell: recipientOf },
    { key: 'text', header: '질문', cell: q => shortText(q.question_text, 90) },
    { key: 'act', header: '', width: 86, className: 'c', cell: q => <button type="button" className="kb" aria-pressed={picked === q.question_id} onClick={() => { setNotice(''); setPicked(q.question_id); }}>회신 기록</button> },
  ];
  return <KvModal title={`회신 기록 · ${groupName}`} onClose={close} className="rfp-dialog" keepOnBackdrop>
    {questions.isPending ? <Loading label="질문 기록 읽는 중" /> : questions.isError ? <ErrorState error={questions.error} onRetry={() => questions.refetch()} /> : <>
      <p className="rfp-dialog-lead">받은 회신을 질문별로 기록합니다. 고객 회신이 요구값을 채우면 변경 제안이 생기고, 승인 전에는 요구가 바뀌지 않습니다.</p>
      <KvGrid label="회신 기다리는 질문" columns={columns} rows={open} rowKey={q => q.question_id} selectedKey={picked} fallback="회신을 기다리는 질문이 없습니다." scroll />
      {question && <ReplyForm key={question.question_id} question={question} onCancel={() => setPicked(undefined)} onReload={() => questions.refetch()} onSaved={proposed => { setPicked(undefined); setNotice(proposed ? '회신을 기록했습니다. 변경 제안이 생겼으니 승인 여부를 정하세요.' : '회신을 기록했습니다.'); setReplied(true); void client.invalidateQueries({ queryKey: ['rfp-questions'] }); }} />}
      {notice && <p role="status">{notice}</p>}
    </>}
    <div className="rfp-dialog-actions"><button type="button" className="kb" onClick={() => { if (replied) refreshAfterWrite(client); onOutbox(); }}>보낼 내용 보기</button><button type="button" className="kb primary" onClick={close}>닫기</button></div>
  </KvModal>;
}

const pathTitle = (path: string, task: boolean) => task && path === 'OWN_DEVELOPMENT' ? '자사 수행' : task && path === 'TASK_SUBCONTRACT' ? '협력사 위탁' : label(PATH_LABEL, path);

// 같은 대응 방안을 여러 요구에 기록한다: 고르기 → 기록 전 확인(쓰지 않는 점검) → 기록. 확인 없이 저장하지 않는다.
export function SelectionDialog({ projectId, actorId, rows, task, groupName, onClose }: { projectId: string; actorId: string; rows: RequirementSummary[]; task: boolean; groupName: string; onClose: () => void }) {
  const mode = getApiMode(), client = useQueryClient();
  const project = useProject(projectId);
  const [step, setStep] = useState<'pick' | 'confirm' | 'done'>('pick');
  const [checked, setChecked] = useState(() => new Set(rows.slice(0, LIMIT).map(r => r.requirement_id)));
  const [path, setPath] = useState('');
  const [roleSplit, setRoleSplit] = useState('');
  const [conditions, setConditions] = useState('');
  const [note, setNote] = useState('');
  const [waiver, setWaiver] = useState('');
  const [check, setCheck] = useState<SupplementBatchResult>();
  const [saved, setSaved] = useState<SupplementBatchResult>();
  const picked = rows.filter(r => checked.has(r.requirement_id));
  const options = useQuery({
    queryKey: ['rfp-batch-options', mode, actorId, projectId, picked.map(r => `${r.requirement_id}:${r.version}`)], retry: false, refetchOnWindowFocus: false,
    queryFn: () => mapLimited(picked, 4, row => getSupplementOptions(row.requirement_id).then(r => r.data.requirement_id === row.requirement_id && r.data.version === row.version ? r.data as SupplementOptions : null, () => null)),
  });
  const current = picked.map((_, index) => options.data?.[index]);
  const loading = options.isPending;
  const unread = current.filter(c => c === null).length;
  const paths = new Map<string, { rank: number; count: number }>();
  for (const opt of current) for (const o of opt?.options ?? []) if (o.allowed && o.path !== 'NONE') paths.set(o.path, { rank: Math.min(o.rank, paths.get(o.path)?.rank ?? Infinity), count: (paths.get(o.path)?.count ?? 0) + 1 });
  const pathList = [...paths].sort((a, b) => a[1].rank - b[1].rank);
  const needsWaiver = current.some(opt => { const o = opt?.options.find(x => x.path === path); return o?.gate.requires === 'OWN_DEVELOPMENT_CHECK' && o.gate.status === 'PENDING'; });
  const items = picked.flatMap((row, index) => current[index] ? [{ requirement_id: row.requirement_id, version: current[index]!.version }] : []);
  const base = { path: path as SupplementOptions['options'][number]['path'], role_split: roleSplit.trim(), conditions: conditions.split('\n').map(c => c.trim()).filter(Boolean), note: note.trim() || null, candidate_codes: [], rnd_check_waiver_reason: needsWaiver ? waiver.trim() : null };
  const allowedItems = items.filter(item => check?.allowed.some(a => a.requirement_id === item.requirement_id));
  const [key, nextKey] = useAttemptKey(JSON.stringify([base, allowedItems]));
  const dry = useMutation({ mutationFn: async () => { await sameActor(actorId); return selectSupplementBatch(projectId, { ...base, dry_run: true, items }); }, retry: false, onSuccess: result => { setCheck(result.data); setStep('confirm'); } });
  const save = useMutation({
    mutationFn: async () => { await sameActor(actorId); return selectSupplementBatch(projectId, { ...base, dry_run: false, items: allowedItems }, key); }, retry: false,
    onSuccess: result => { nextKey(); setSaved(result.data); setStep('done'); }, onError: error => { if (settled(error)) nextKey(); },
  });
  const close = () => { if (saved) refreshAfterWrite(client); onClose(); };
  const canWrite = isOwner(project, actorId);
  const ready = canWrite && !loading && !!path && !!roleSplit.trim() && items.length > 0 && (!needsWaiver || waiver.trim().length >= 20) && !dry.isPending;
  const rowOf = (id: string) => rows.find(row => row.requirement_id === id);
  const reasonColumns: KvColumn<{ requirement_id: string; code: string; message: string }>[] = [
    { key: 'no', header: '요구', width: 110, cell: b => { const row = rowOf(b.requirement_id); return row ? requirementNo(row.code) : '번호 미확인'; } },
    { key: 'subject', header: '내용', width: 200, cell: b => { const row = rowOf(b.requirement_id); return row ? subjectOf(row) : ''; } },
    { key: 'why', header: '막힌 이유', cell: b => itemReason(b.code, b.message) },
  ];
  const listColumns: KvColumn<{ requirement_id: string }>[] = [
    { key: 'no', header: '요구', width: 110, cell: a => { const row = rowOf(a.requirement_id); return row ? requirementNo(row.code) : '번호 미확인'; } },
    { key: 'subject', header: '내용', cell: a => { const row = rowOf(a.requirement_id); return row ? `${subjectOf(row)}${valueOf(row) ? ` · ${shortText(valueOf(row), 40)}` : ''}` : ''; } },
  ];
  const failure = (error: unknown) => unsupportedRoute(error) ? <p role="alert">{UNSUPPORTED}</p> : <ErrorState error={error} />;
  const title = pathTitle(path, task);
  return <KvModal title={`${task ? '수행 주체 정하기' : '같은 대응 방안으로 기록'} · ${groupName}`} onClose={close} className="rfp-dialog" keepOnBackdrop>
    <Steps steps={['방안 고르기', '기록 전 확인', '기록 결과']} current={{ pick: 0, confirm: 1, done: 2 }[step]} />
    {step === 'pick' && <>
      <PickRows rows={rows} checked={checked} onChange={next => { setChecked(next); setCheck(undefined); }} />
      <fieldset className="rfp-dialog-paths"><legend>{task ? '이 과업을 누가 수행합니까?' : '어떻게 보완할까요?'}</legend>
        {loading ? <Loading label="고를 수 있는 방안 확인 중" /> : pathList.length ? pathList.map(([code, info]) => <label key={code}><input type="radio" name="batch-path" checked={path === code} onChange={() => setPath(code)} /><b>{pathTitle(code, task)}</b><span>{info.count === picked.length ? `고른 ${picked.length}건 모두 가능` : `${picked.length}건 중 ${info.count}건 가능 · 나머지는 기록 전 확인에서 빠집니다`}</span></label>)
          : <p>고른 요구에 함께 기록할 수 있는 방안이 없습니다. 요구를 하나씩 열어 확인하세요.</p>}
      </fieldset>
      {unread > 0 && <p role="alert">{unread}건은 대응 방안을 읽지 못했습니다. <button type="button" className="kb" onClick={() => options.refetch()}>다시 읽기</button></p>}
      <label className="rfp-dialog-field">{task ? '수행 담당과 업무 범위' : '담당자와 후속 확인 사항'} (필수)<textarea rows={2} maxLength={2000} value={roleSplit} placeholder={task ? '예: 기술지원이 시험 범위를 검토하고 제출 자료를 준비' : '예: 기술 담당이 적용 조건과 추가 자료를 확인'} onChange={e => setRoleSplit(e.target.value)} /></label>
      <label className="rfp-dialog-field">아직 확인되지 않은 조건<textarea rows={2} value={conditions} placeholder="한 줄에 하나" onChange={e => setConditions(e.target.value)} /></label>
      <label className="rfp-dialog-field">메모<input value={note} maxLength={1000} onChange={e => setNote(e.target.value)} /></label>
      {needsWaiver && <label className="rfp-dialog-field">R&D 확인 면제 사유 (20자 이상)<textarea rows={2} minLength={20} maxLength={1000} value={waiver} onChange={e => setWaiver(e.target.value)} /></label>}
      {dry.isError && failure(dry.error)}
      <p className="rfp-group-note">기록 전 확인은 저장하지 않습니다. 기록될 요구와 막힌 요구를 먼저 확인하세요.{!canWrite && project.isSuccess ? ' 프로젝트 담당자·책임자만 기록할 수 있습니다.' : ''}</p>
      <div className="rfp-dialog-actions"><button type="button" className="kb" onClick={onClose}>취소</button><button type="button" className="kb primary" disabled={!ready} onClick={() => dry.mutate()}>{dry.isPending ? '확인 중' : '기록 전 확인'}</button></div>
    </>}
    {step === 'confirm' && check && <>
      <p className="rfp-dialog-lead">'{title}'을(를) 기록합니다. 기록될 요구 {check.allowed.length}건 · 막힌 요구 {check.blocked.length}건</p>
      {path === 'OWN_DEVELOPMENT' && <p className="rfp-group-note">요구마다 R&D 확인 질문이 함께 기록됩니다.</p>}
      <Section title={`기록될 요구 ${check.allowed.length}건`}><KvGrid label="기록될 요구" columns={listColumns} rows={check.allowed} rowKey={a => a.requirement_id} fallback="기록될 요구가 없습니다." scroll /></Section>
      {check.blocked.length > 0 && <Section title={`막힌 요구 ${check.blocked.length}건`}><KvGrid label="막힌 요구" columns={reasonColumns} rows={check.blocked} rowKey={b => b.requirement_id} fallback="" scroll /></Section>}
      {save.isError && failure(save.error)}
      <div className="rfp-dialog-actions"><button type="button" className="kb" onClick={() => { save.reset(); setStep('pick'); }}>다시 고르기</button><button type="button" className="kb primary" disabled={!allowedItems.length || save.isPending} onClick={() => save.mutate()}>{save.isPending ? '기록 중' : `요구 ${allowedItems.length}건에 기록`}</button></div>
    </>}
    {step === 'done' && saved && <>
      <p role="status" className="rfp-dialog-lead">요구 {saved.saved.length}건에 '{title}'을(를) 기록했습니다.</p>
      {saved.blocked.length > 0 && <Section title={`기록하지 못한 요구 ${saved.blocked.length}건`}><KvGrid label="기록하지 못한 요구" columns={reasonColumns} rows={saved.blocked} rowKey={b => b.requirement_id} fallback="" scroll /></Section>}
      <div className="rfp-dialog-actions"><button type="button" className="kb primary" onClick={close}>닫기</button></div>
    </>}
  </KvModal>;
}
const Section = ({ title, children }: { title: string; children: ReactNode }) => <section className="rfp-dialog-section"><h3>{title}</h3>{children}</section>;
