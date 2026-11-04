import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { me } from '../api/platform';
import {
  createImprovementAiDraft, getImprovementAiDraft, reviewImprovement, updateImprovement,
  type AiDraftRecord, type ImprovementDetail, type ImprovementPatch,
} from '../api/cs-improvements';
import { DEPT_CODES, deptName, formatStamp } from '../ls-shell/cs-labels';

type Request = ImprovementDetail['data']['request'];
type Fact = AiDraftRecord['confirmed_facts'][number];
type Dept = NonNullable<ImprovementPatch['proposed_depts']>[number];

// 요청 내용 칸 이름. 현재 내용과 AI 초안이 같은 이름을 쓴다.
export const CONTENT_LABEL = {
  problem_statement: '검토할 문제', confirmed_facts: '확인된 사실', inconsistencies: '불일치 정보',
  missing_info: '미확인 정보', review_task: '검토 과제', not_claimed: '단정하지 않는 내용',
} as const;
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const lines = (items: string[]) => items.join('\n') || null;
type Field = keyof typeof CONTENT_LABEL;
// 입력 칸을 내용 높이만큼 늘린다(칸 안 스크롤 없음).
const fit = (el: HTMLTextAreaElement | null) => { if (!el) return; el.style.height = 'auto'; el.style.height = `${el.scrollHeight + 2}px`; };
const FIELDS = Object.keys(CONTENT_LABEL) as Field[];

function Glint() {
  return <span className="glint" aria-hidden="true">
    <svg className="glint-core" viewBox="0 0 24 24"><path d="M12 1.5c.6 5.2 2.3 8.4 10.5 10.5C14.3 14.1 12.6 17.3 12 22.5 11.4 17.3 9.7 14.1 1.5 12 9.7 9.9 11.4 6.7 12 1.5Z" /></svg>
    <span className="glint-orbit"><i /><i /><i /></span>
  </span>;
}

/**
 * 요청 내용 상자. 문의 처리의 "✦ AI 분석 실행"처럼 머리의 버튼으로 AI 작성 초안(KAN-115)을 만든다.
 * 반영 전 저장된 초안이 있으면 초안을, 아니면 현재 내용을 보인다. 초안은 요청을 바꾸지 않는다.
 * 초안 아래 "검토 완료"는 초안을 그대로 저장(PATCH)한 뒤 검토 완료를 기록하고, "수정"은 초안 값으로 입력 칸을 연다.
 * 현재 내용은 머리의 "내용 수정"으로 이 자리에서 고친다. 저장은 새 판본이 되며 검토·전달은 하지 않는다.
 * onPending: 검토 완료를 막을 이유(내용이 비었거나 반영하지 않은 초안이 있음)를 상위 화면에 알린다.
 */
// ruleOnly: 시스템 초안이 만들 때 규칙으로 채운 내용 그대로다(사람·AI가 아직 쓰지 않음). 그 값은 보이지 않고 AI 초안 작성을 안내한다.
// next: 현재 내용 아래 버튼 자리에 둘 다음 동작(검토 완료 뒤의 "부서 전달"). 입력은 수신 부서 상자 위에 열린다.
export default function ImprovementContent({ request: r, title, tags, locked, onSaved, onReload, onEditingChange, onPending, next, ruleOnly = false }: {
  request: Request; title: string; tags: ReactNode; locked: boolean; onSaved: (result: ImprovementDetail) => void; onReload: () => void;
  onEditingChange?: (editing: boolean) => void; onPending?: (reason: string | null) => void; next?: ReactNode; ruleOnly?: boolean;
}) {
  const client = useQueryClient();
  const mode = getApiMode();
  const live = mode === 'live';
  const key = ['cs-improvement-ai-draft', mode, r.id];
  // 요청 판본이 바뀌면(근거 추가·빼기 포함) 다시 읽어 오래된 초안인지 서버 판정을 받는다.
  const ai = useQuery({ queryKey: [...key, r.version], queryFn: ({ signal }) => getImprovementAiDraft(r.id, signal), retry: false, staleTime: 0 });
  const identity = useQuery({ queryKey: ['cs-improvement-actor', mode], queryFn: me, enabled: live, retry: false, staleTime: 0 });
  const [phase, setPhase] = useState<'idle' | 'running' | 'checking'>('idle');
  const [finished, setFinished] = useState<'ok' | 'fail' | null>(null);
  const [note, setNote] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [applying, setApplying] = useState(false);
  // 열린 입력 칸. 제안 부서도 여기서 고른다(부서 전달은 이 제안 부서로 바로 보낸다).
  const [editing, setEditing] = useState<{ values: Record<Field, string>; depts: Dept[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  useEffect(() => { onEditingChange?.(editing !== null); return () => onEditingChange?.(false); }, [editing, onEditingChange]);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  const off = ai.isError && ai.error instanceof ApiError && ai.error.status === 404;
  const draft = ai.data?.data.draft ?? null;
  const lastFailed = ai.data?.data.latest_attempt && ai.data.data.latest_attempt.outcome !== 'VALID'
    && (!draft || Date.parse(ai.data.data.latest_attempt.created_at) > Date.parse(draft.created_at));
  const roles = Array.isArray(identity.data?.data?.roles) ? identity.data!.data.roles as string[] : [];
  const completed = r.status === 'COMPLETED';
  const why = !live ? '모의 응답에서는 AI 초안을 만들지 않습니다'
    : off ? 'AI 초안 기능이 이 배포에서 꺼져 있습니다'
      : completed ? '완료된 요청입니다'
        : identity.isSuccess && !roles.includes('cs_agent') ? 'C/S 담당자만 실행할 수 있습니다'
          : locked ? '다른 입력이 열려 있습니다. 저장하거나 취소한 뒤 실행하세요' : null;
  const store = (data: NonNullable<typeof ai.data>) => client.setQueryData([...key, r.version], data);

  async function run() {
    if (phase !== 'idle' || why) return;
    const current = new AbortController(); controller.current = current;
    const startedAt = Date.now();
    setPhase('running'); setFinished(null); setNote(null);
    try {
      const result = await createImprovementAiDraft(r.id, current.signal);
      if (current.signal.aborted) return;
      store(result); setFinished('ok');
      setNote({ kind: 'ok', text: 'AI 초안을 만들었습니다. 확인한 뒤 "검토 완료"로 그대로 반영하거나 "수정"으로 고쳐 저장하세요.' });
    } catch (error) {
      if (current.signal.aborted) return;
      // 중계 시간 초과·연결 끊김은 서버가 계속 만들고 있을 수 있다. 다시 보내지 않고 저장본을 몇 번 다시 읽는다.
      const unknown = !(error instanceof ApiError) || error.status === 408 || error.status === 504;
      if (unknown) {
        setPhase('checking');
        for (let i = 0; i < 20 && !current.signal.aborted; i++) {
          await sleep(3000);
          const read = await getImprovementAiDraft(r.id, current.signal).catch(() => null);
          const made = read?.data.draft;
          if (read && made && Date.parse(made.created_at) >= startedAt - 5000) {
            store(read); setFinished('ok');
            setNote({ kind: 'ok', text: 'AI 초안을 만들었습니다. 확인한 뒤 "검토 완료"로 그대로 반영하거나 "수정"으로 고쳐 저장하세요.' });
            return;
          }
          const failed = read?.data.latest_attempt;
          if (read && failed && failed.outcome !== 'VALID' && Date.parse(failed.created_at) >= startedAt - 5000) break;
        }
        if (current.signal.aborted) return;
        void ai.refetch(); setFinished('fail');
        setNote({ kind: 'error', text: 'AI 초안 결과를 아직 확인하지 못했습니다. 잠시 뒤 "최신 요청 조회"로 다시 확인하세요.' });
        return;
      }
      void ai.refetch(); setFinished('fail');
      setNote({ kind: 'error', text: error.code === 'CS_IMPROVEMENT_DRAFT_FAILED' ? 'AI 초안을 만들지 못했습니다. 다시 누르면 새로 만듭니다(비용).'
        : error.status === 404 ? 'AI 초안 기능이 이 배포에서 꺼져 있습니다.' : error.message });
    } finally {
      if (controller.current === current) controller.current = null;
      if (!current.signal.aborted) setPhase('idle');
    }
  }

  const validDepts = (codes: string[]) => codes.filter((code): code is Dept => (DEPT_CODES as readonly string[]).includes(code));
  const draftValues = (d: AiDraftRecord): Record<Field, string> => ({
    problem_statement: d.problem_statement, confirmed_facts: d.confirmed_facts.map(f => f.text).join('\n'), inconsistencies: d.inconsistencies.map(f => f.text).join('\n'),
    missing_info: d.missing_info.join('\n'), not_claimed: d.not_claimed.join('\n'),
    // 기획서 10쪽 "제안 부서와 검토 과제": 요청서에 부서별 질문 칸이 없으므로 검토 과제 아래에 "부서: 질문" 줄로 붙여 함께 전달한다.
    review_task: [d.review_task, ...d.dept_questions.map(q => `${deptName(q.dept_code)}: ${q.question}`)].filter(Boolean).join('\n'),
  });
  const failText = (error: unknown) => error instanceof ApiError && error.status === 409 ? '다른 곳에서 요청이 먼저 바뀌었습니다. "최신 요청 조회"로 다시 읽은 뒤 확인하세요.'
    : error instanceof ApiError && error.status < 500 && error.status !== 408 ? error.message : '저장 결과를 확인할 수 없습니다. 다시 누르지 말고 "최신 요청 조회"로 확인하세요.';

  // 초안을 그대로 저장하고, 작성 중 요청이면 이어서 검토 완료를 기록한다. 검토만 실패하면 저장된 내용은 그대로 보인다.
  async function applyDraft() {
    if (!draft || applying || locked || completed || !live) return;
    const current = new AbortController(); controller.current = current;
    setApplying(true); setNote(null);
    const depts = validDepts(draft.proposed_depts);
    const values = draftValues(draft);
    const body: ImprovementPatch = {
      version: r.version, problem_statement: values.problem_statement,
      confirmed_facts: values.confirmed_facts || null, inconsistencies: values.inconsistencies || null,
      missing_info: values.missing_info || null, not_claimed: values.not_claimed || null, review_task: values.review_task || null,
      ...(depts.length ? { proposed_depts: depts } : {}),
    };
    const review = r.status === 'DRAFT';
    try {
      const saved = await updateImprovement(r.id, body, current.signal);
      if (current.signal.aborted) return;
      if (!review) { setNote({ kind: 'ok', text: `AI 초안으로 내용을 바꿨습니다(판본 ${saved.data.request.version}).` }); onSaved(saved); return; }
      try {
        const reviewed = await reviewImprovement(r.id, { version: saved.data.request.version }, current.signal);
        if (current.signal.aborted) return;
        setNote({ kind: 'ok', text: `AI 초안으로 내용을 저장하고 검토 완료를 기록했습니다(판본 ${reviewed.data.request.version}). 다음은 부서 전달입니다.` });
        onSaved(reviewed);
      } catch (error) {
        if (current.signal.aborted) return;
        onSaved(saved);
        setNote({ kind: 'error', text: `내용은 저장했지만 검토 완료를 기록하지 못했습니다. ${failText(error)}` });
      }
    } catch (error) {
      if (current.signal.aborted) return;
      setNote({ kind: 'error', text: failText(error) });
    } finally {
      if (controller.current === current) controller.current = null;
      if (!current.signal.aborted) setApplying(false);
    }
  }

  const openEditor = (fromDraft: boolean) => {
    if (locked || completed || !live) return;
    // 규칙 값만 있는 시스템 초안은 빈 칸에서 시작한다(화면에 보이는 그대로).
    const values = fromDraft && draft ? draftValues(draft)
      : Object.fromEntries(FIELDS.map(f => [f, ruleOnly ? '' : r[f] ?? ''])) as Record<Field, string>;
    const draftDepts = fromDraft && draft ? validDepts(draft.proposed_depts) : [];
    setEditing({ values, depts: draftDepts.length ? draftDepts : validDepts(r.proposed_depts) });
    setSaveError(''); setUncertain(false); setNote(null);
  };
  const changes: ImprovementPatch = { version: r.version };
  if (editing) {
    for (const f of FIELDS) {
      const value = editing.values[f].trim();
      if (value !== (r[f] ?? '')) (changes as Record<string, unknown>)[f] = f === 'problem_statement' ? value : value || null;
    }
    if (editing.depts.length !== r.proposed_depts.length || editing.depts.some(d => !r.proposed_depts.includes(d))) changes.proposed_depts = editing.depts;
  }
  const dirty = Object.keys(changes).length > 1;
  // 고장 요청은 제안 부서가 1개 이상 있어야 한다(계약 CS_DEPARTMENT_REQUIRED).
  const deptsOk = !editing || r.request_type !== 'FAULT' || editing.depts.length > 0;
  async function saveEdit() {
    if (!editing || saving || uncertain || !dirty || !deptsOk || !editing.values.problem_statement.trim()) return;
    const current = new AbortController(); controller.current = current;
    setSaving(true); setSaveError('');
    try {
      const result = await updateImprovement(r.id, changes, current.signal);
      if (current.signal.aborted) return;
      setEditing(null);
      setNote({ kind: 'ok', text: `내용을 저장했습니다(판본 ${result.data.request.version}).${r.status === 'DRAFT' ? ' 확인이 끝나면 단계 바의 "검토 완료 기록"을 누르세요.' : ''}` });
      onSaved(result);
    } catch (error) {
      if (current.signal.aborted) return;
      setSaveError(error instanceof ApiError ? error.message : '저장 결과를 확인할 수 없습니다. 최신 내용을 다시 조회하세요.');
      setUncertain(!(error instanceof ApiError) || error.status === 408 || error.status === 409 || error.status >= 500);
    } finally {
      if (controller.current === current) controller.current = null;
      if (!current.signal.aborted) setSaving(false);
    }
  }

  const busy = phase !== 'idle';
  // 저장된 AI 초안이 있고 아직 내용에 반영하지 않았으면 초안을 보인다(새로고침해도 서버 저장본을 다시 읽어 보인다).
  // 반영했으면(검토할 문제가 같음) 현재 내용을 보인다.
  const showAi = !!draft && draft.problem_statement !== r.problem_statement;
  const pending = showAi ? '반영하지 않은 AI 초안이 있습니다. 개선 요청 작성의 "검토 완료"나 "수정"으로 반영하세요.'
    : ruleOnly ? '요청 내용이 비어 있습니다. "✦ AI 초안 작성"으로 초안을 만들거나 "내용 수정"으로 직접 쓴 뒤 검토하세요.' : null;
  useEffect(() => { onPending?.(pending); }, [pending, onPending]);
  useEffect(() => () => onPending?.(null), [onPending]);
  const editable = live && !completed && !locked && !busy && !applying;
  const editor = editing && <form className="sendbox ir-edit" aria-busy={saving} onSubmit={e => { e.preventDefault(); void saveEdit(); }}>
    {FIELDS.map(f => <label key={f}>{CONTENT_LABEL[f]}<textarea ref={fit} rows={1} autoFocus={f === 'problem_statement'} required={f === 'problem_statement'} maxLength={10000} disabled={saving}
      value={editing.values[f]} onChange={e => { fit(e.target); setEditing({ ...editing, values: { ...editing.values, [f]: e.target.value } }); }} /></label>)}
    <div className="dept-pick">
      <span className="k">제안 부서{r.request_type === 'FAULT' ? ' (1개 이상)' : ''}</span>
      <div className="chips">
        {editing.depts.map(code => <span key={code} className="dept-chip">{deptName(code)}
          <button type="button" className="x" aria-label={`${deptName(code)} 빼기`} disabled={saving} onClick={() => setEditing({ ...editing, depts: editing.depts.filter(x => x !== code) })}>×</button></span>)}
        <select aria-label="제안 부서 추가" value="" disabled={saving || editing.depts.length === DEPT_CODES.length}
          onChange={e => { const code = e.target.value as Dept; if (code) setEditing({ ...editing, depts: [...editing.depts, code] }); }}>
          <option value="">+ 부서 추가</option>
          {DEPT_CODES.filter(code => !editing.depts.includes(code as Dept)).map(code => <option key={code} value={code}>{deptName(code)}</option>)}
        </select>
      </div>
    </div>
    {saveError && <p role="alert">{saveError}</p>}
    {uncertain && <p className="stale">충돌하거나 저장 결과가 불확실합니다. 다시 저장하지 말고 최신 내용을 확인하세요.</p>}
    <div className="row2">
      {uncertain && <button className="kb3 sm" type="button" onClick={() => { setEditing(null); onReload(); }}>입력 버리고 최신 내용 조회</button>}
      <button className="kb3 sm" type="button" disabled={saving} onClick={() => { setEditing(null); setSaveError(''); setUncertain(false); }}>수정 취소</button>
      <button className="kb3 sm pri" type="submit" disabled={saving || uncertain || !dirty || !deptsOk || !editing.values.problem_statement.trim()}>{saving ? '저장 중' : '내용 저장'}</button>
    </div>
  </form>;
  const refOf = new Map((draft?.evidence ?? []).map(e => [e.ref, e.ticket_id]));
  const refs = (fact: Fact) => fact.evidence_refs.map(ref => refOf.get(ref)
    ? <Link key={ref} className="ref" to={`/cs/tickets/${encodeURIComponent(refOf.get(ref)!)}`}>{ref}</Link>
    : <span key={ref} className="ref">{ref}</span>);
  const factList = (items: Fact[], kind: string) => items.length
    ? <ul className={`fact-box ${kind} ai-list`}>{items.map((f, i) => <li key={i}>{f.text}{f.evidence_refs.length > 0 && <span className="refs">{refs(f)}</span>}</li>)}</ul>
    : <div className="fact-box empty">없음</div>;
  const textList = (items: string[], kind: string) => items.length
    ? <ul className={`fact-box ${kind} ai-list`}>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    : <div className="fact-box empty">없음</div>;
  const row = (label: string, body: ReactNode) => <div className="fact-row"><span className="k">{label}</span>{body}</div>;
  function deptChips(codes: string[]) { return <div className="chips">{codes.length ? codes.map(x => <span key={x} className="dept-chip">{deptName(x)}</span>) : <span className="fact-box empty">미확인</span>}</div>; }

  return <div className="pane ir-content">
    <div className="ph">개선 요청 작성
      <div className="r">
        {!showAi && !editing && <button className="kb3 sm" disabled={!editable} title={completed ? '완료된 요청은 고칠 수 없습니다' : locked ? '다른 입력이 열려 있습니다' : '요청 내용을 이 자리에서 고칩니다. 저장하면 새 판본이 됩니다'} onClick={() => openEditor(false)}>내용 수정</button>}
        <button className="kb3 sm" disabled={!!why || busy || applying || editing !== null} title={why ?? `근거 문의를 읽고 요청 내용 초안을 씁니다(모델 호출 비용). 근거가 같으면 이미 만든 초안을 다시 보여 줍니다.${draft ? ` 마지막 초안 ${formatStamp(draft.created_at)}` : ''}`} onClick={() => void run()}>
          {phase === 'running' ? '✦ 작성 중…' : phase === 'checking' ? '✦ 결과 확인 중…' : '✦ AI 초안 작성'}
        </button>
      </div>
    </div>
    <div className="pb">
      {(busy || finished) && <div className={`ai-progress${busy ? '' : ' stopped'}${finished === 'fail' ? ' failed' : ''}`} role="status" aria-live="polite">
        <Glint />
        <b>{phase === 'running' ? 'AI가 근거 문의를 읽고 요청 내용을 쓰는 중' : phase === 'checking' ? '응답이 늦어 저장된 결과를 확인하는 중' : finished === 'ok' ? 'AI 초안 작성 완료' : 'AI 초안을 만들지 못했습니다'}</b>
      </div>}
      {note && <div className={note.kind === 'ok' ? 'ai-note' : 'ai-note err'} role={note.kind === 'ok' ? 'status' : 'alert'}>{note.text}</div>}
      {!busy && !finished && lastFailed && <div className="meta">마지막 AI 초안 시도가 실패했습니다({formatStamp(ai.data!.data.latest_attempt!.created_at)}).</div>}

      {!showAi ? <>
        <h2 className="problem">{title}</h2>
        {tags}
        {r.receiver_unconfirmed && <div className="warn-note">수신 부서 미확인 — 전달 준비가 끝난 상태가 아닙니다.</div>}
        {/* 규칙 값만 있는 시스템 초안은 칸을 비운 채(-) 보인다. 문의 처리의 AI 분석 전 칸과 같다. */}
        {editor || <div className="facts">
          {([['confirmed_facts', 'fact'], ['inconsistencies', 'conflict'], ['missing_info', 'missing'], ['review_task', 'task'], ['not_claimed', 'claim']] as const).map(([field, kind]) => {
            const value = ruleOnly ? null : r[field];
            return <div key={field} className="fact-row"><span className="k">{CONTENT_LABEL[field]}</span><div className={value ? `fact-box ${kind}` : 'fact-box empty'}>{value ?? '-'}</div></div>;
          })}
          {row('사용 조건', <div className={!ruleOnly && r.condition_summary ? 'fact-box' : 'fact-box empty'}>{(!ruleOnly && r.condition_summary) || '-'}</div>)}
          {row('제안 부서', ruleOnly ? <div className="fact-box empty">-</div> : deptChips(r.proposed_depts))}
        </div>}
        {!editing && next && <div className="row2 ai-apply"><span className="btns2">{next}</span></div>}
        {!editing && (ruleOnly || !draft) && !busy && !off && <div className="meta">{ruleOnly ? '아직 요청 내용이 없습니다. ' : ''}위의 "✦ AI 초안 작성"을 누르면 근거 문의를 읽고 내용을 씁니다.</div>}
      </> : <>
        <div className="ai-head"><span className="ai">✦ AI 초안</span><span className="meta">{formatStamp(draft.created_at)} · 판본 {draft.request_version} 기준</span></div>
        {draft.stale && <div className="stale">이 초안을 만든 뒤 근거가 바뀌었습니다. "✦ AI 초안 작성"으로 새로 만드세요.</div>}
        {editor || <><h2 className="problem">{draft.problem_statement}</h2>
        <div className="facts">
          {row(CONTENT_LABEL.confirmed_facts, factList(draft.confirmed_facts, 'fact'))}
          {row(CONTENT_LABEL.inconsistencies, factList(draft.inconsistencies, 'conflict'))}
          {row(CONTENT_LABEL.missing_info, textList(draft.missing_info, 'missing'))}
          {row(CONTENT_LABEL.review_task, draft.review_task ? <div className="fact-box task">{draft.review_task}</div> : <div className="fact-box empty">없음</div>)}
          {row(CONTENT_LABEL.not_claimed, textList(draft.not_claimed, 'claim'))}
          {row('제안 부서', deptChips(draft.proposed_depts))}
          {row('부서별 질문', draft.dept_questions.length
            ? <ul className="fact-box ask ai-list">{draft.dept_questions.map((q, i) => <li key={i}>{q.question}</li>)}</ul>
            : <div className="fact-box empty">없음</div>)}
        </div>
        <div className="row2 ai-apply">
          <span className="btns2"><button className="kb3 sm" disabled={!editable || draft.stale} title={draft.stale ? '근거가 바뀐 초안입니다. 새로 만든 뒤 고치세요' : '초안 값으로 입력 칸을 열어 고친 뒤 저장합니다'} onClick={() => openEditor(true)}>수정</button>
          <button className="kb3 sm pri" disabled={!editable || draft.stale}
            title={draft.stale ? '근거가 바뀐 초안입니다. 새로 만든 뒤 반영하세요' : locked ? '다른 입력이 열려 있습니다' : r.status === 'DRAFT' ? '이 초안을 그대로 저장하고 검토 완료를 기록합니다' : '이 초안을 그대로 저장합니다'}
            onClick={() => void applyDraft()}>{applying ? '저장 중' : r.status === 'DRAFT' ? '검토 완료' : '그대로 반영'}</button></span>
        </div>
        </>}
      </>}
    </div>
  </div>;
}
