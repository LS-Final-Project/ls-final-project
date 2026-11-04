import { useMutation } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { ApiError } from '../api/client';
import { me } from '../api/platform';
import { createQuestion, replyQuestion, type Question, type RequirementDetail } from '../api/rfp';
import { KvBox, KvField } from '../ls-shell/kv';
import { DEPARTMENT_LABEL, FEASIBILITY_LABEL, ISSUE_LABEL, label, requirementNo, RND_DEPARTMENTS } from './labels';
import { Failure, settled, useAttemptKey } from './proposal-shared';

// 내부 자문 유형은 INTERNAL 전용이다. R&D 확인은 R&D 부서만 답할 수 있어 다른 부서에는 내지 않는다.
const CUSTOMER_ISSUES = ['EMPTY_VALUE', 'CONFLICT', 'SCOPE_UNCLEAR', 'CONDITION_MISSING'];
const CUSTOMER = 'CUSTOMER';
const issuesFor = (target: string) => {
  if (target === CUSTOMER) return CUSTOMER_ISSUES;
  return [...(RND_DEPARTMENTS.includes(target) ? ['RND_FEASIBILITY'] : []), 'TECH_ADVICE', 'PURCHASE_TERMS'];
};
const clauseOf = ({ origin }: RequirementDetail) => [origin.clause_no, origin.page != null ? `p.${origin.page}` : ''].filter(Boolean).join(', ');
const trimmed = (text: string) => text.trim() || null;

// 고객 질문 작성(TF_K_Req_Question). 가정값은 충족 판정에 쓰지 않는다.
// ponytail: 승인자를 고를 사용자 목록 API가 없어 승인자는 비우고 초안 표시는 끈다. 승인 없는 가정이 고객 초안에 나가면 안 된다.
export function QuestionForm({ projectId, requirement, departments, onCancel, onSaved, initialTarget, actorId, useMockFallback = true, audience, initialIssue, initialText = '', disabled = false }: {
  initialTarget?: string;
  audience?: 'INTERNAL' | 'CUSTOMER';
  initialIssue?: Question['issue_type'];
  initialText?: string;
  disabled?: boolean;
  actorId?: string;
  useMockFallback?: boolean;
  projectId: string;
  requirement: RequirementDetail;
  // 프로젝트 검토 부서. 내부 질문은 이 안에서만 서버가 받는다.
  departments: string[];
  onCancel: () => void;
  onSaved: () => void;
}) {
  const formId = useId();
  const fieldId = (name: string) => `${formId}-${name}`;
  const internalOnly = audience === 'INTERNAL' || audience !== 'CUSTOMER' && !!initialTarget;
  const fixedTarget = audience !== 'CUSTOMER' && initialTarget && departments.includes(initialTarget) ? initialTarget : undefined;
  const startingTarget = fixedTarget ?? (internalOnly ? '' : CUSTOMER);
  const [target, setTarget] = useState(startingTarget);
  const [issue, setIssue] = useState(initialIssue && issuesFor(startingTarget).includes(initialIssue) ? initialIssue : issuesFor(startingTarget)[0]);
  const [text, setText] = useState(initialText);
  const [assumed, setAssumed] = useState('');
  const [standard, setStandard] = useState('');
  const [clause, setClause] = useState('');
  const { clause_id, page } = requirement.origin;
  const body = {
    requirement_id: requirement.requirement_id, origin: { clause_id, page }, issue_type: issue as Question['issue_type'], question_text: text.trim(),
    target: target === CUSTOMER ? 'CUSTOMER' as const : 'INTERNAL' as const, target_dept: target === CUSTOMER ? null : target,
    internal_assumption: target === CUSTOMER && assumed.trim()
      ? { value: assumed.trim(), basis_standard: trimmed(standard), basis_clause: trimmed(clause), approver_user_id: null, shown_in_draft: false }
      : null,
  };
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const create = useMutation({
    mutationFn: async () => {
      if (disabled || !target || target !== CUSTOMER && !departments.includes(target) || internalOnly && target === CUSTOMER || audience === 'CUSTOMER' && target !== CUSTOMER || !issuesFor(target).includes(issue)) throw new ApiError(403, '질문 작성 권한과 검토 부서를 다시 확인하세요.');
      if (actorId) {
        const actor = await me();
        if (!actor || actor.data.id !== actorId) throw new ApiError(409, '세션 사용자가 변경되었습니다. 다시 확인해 주세요.', undefined, 'SESSION_CHANGED');
      }
      return useMockFallback ? createQuestion(projectId, body, key) : createQuestion(projectId, body, key, { useMockFallback: false });
    },
    onSuccess: () => { nextKey(); onSaved(); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  return <div style={{ margin: '8px 14px 0' }}><KvBox title={`질문 작성 · ${requirementNo(requirement.code)}`}>
    <form onSubmit={event => { event.preventDefault(); if (!disabled && target && body.question_text && !create.isPending) create.mutate(); }}>
      <fieldset disabled={disabled || create.isPending} style={{ border: 0, padding: 0, margin: 0 }}>
      <KvField label="원문">{clauseOf(requirement) || '조항 없음'}</KvField>
      <div className="kv-field"><label htmlFor={fieldId('question-target')}>대상</label>
        <select id={fieldId('question-target')} disabled={!!fixedTarget} value={target} onChange={event => { setTarget(event.target.value); setIssue(initialIssue && issuesFor(event.target.value).includes(initialIssue) ? initialIssue : issuesFor(event.target.value)[0]); }}>
          {internalOnly && <option value="">검토 부서 선택</option>}
          {!internalOnly && <option value={CUSTOMER}>고객</option>}
          {audience !== 'CUSTOMER' && departments.map(code => <option key={code} value={code}>{label(DEPARTMENT_LABEL, code)}</option>)}
        </select></div>
      <div className="kv-field"><label htmlFor={fieldId('question-issue')}>유형</label>
        <select id={fieldId('question-issue')} disabled={!!fixedTarget} value={issue} onChange={event => setIssue(event.target.value)}>
          {issuesFor(target).map(code => <option key={code} value={code}>{ISSUE_LABEL[code]}</option>)}
        </select></div>
      <div className="kv-field"><label htmlFor={fieldId('question-text')} className="req">질문</label>
        <textarea id={fieldId('question-text')} rows={3} maxLength={4000} value={text} onChange={event => setText(event.target.value)} /></div>
      {target === CUSTOMER && <div className="kv-field"><label htmlFor={fieldId('question-assumed')}>가정값</label>
        <input id={fieldId('question-assumed')} type="text" placeholder="회신 전 가정값, 선택. 충족 판정에는 쓰지 않음" value={assumed} onChange={event => setAssumed(event.target.value)} /></div>}
      {target === CUSTOMER && assumed.trim() && <>
        <div className="kv-field"><label htmlFor={fieldId('question-standard')}>근거 규격</label><input id={fieldId('question-standard')} type="text" value={standard} onChange={event => setStandard(event.target.value)} /></div>
        <div className="kv-field"><label htmlFor={fieldId('question-clause')}>근거 조항</label><input id={fieldId('question-clause')} type="text" value={clause} onChange={event => setClause(event.target.value)} /></div>
      </>}
      <div className="kv-actions">
        <button type="button" className="kb" onClick={onCancel}>취소</button>
        <button type="submit" className="kb primary" disabled={disabled || !target || !body.question_text || create.isPending}>질문 저장</button>
      </div>
      </fieldset>
      {create.isError && <Failure error={create.error} onReload={onSaved} messages={{}} />}
    </form>
  </KvBox></div>;
}

const REPLY_CONFLICT = { TFT_VERSION_CONFLICT: '이 질문은 이미 회신됐거나 바뀌었습니다. 다시 불러와 확인하세요. 입력한 내용은 남습니다.' };

// 회신 기록(TF_K_Req_Reply). 서버는 고객 회신이 미확인 항목을 줄일 때만 변경 제안을 만든다.
// R&D 확인은 개발 가능성이 있어야 끝나고, 일정 조건부면 일정 부합 여부까지 있어야 끝난다(rnd.rnd_check_of).
export function ReplyForm({ question, onCancel, onSaved, onReload }: {
  question: Question;
  onCancel: () => void;
  onSaved: (proposed: boolean) => void;
  onReload: () => void;
}) {
  const [text, setText] = useState('');
  const [received, setReceived] = useState('');
  const [source, setSource] = useState('');
  const [feasibility, setFeasibility] = useState('');
  const [earliest, setEarliest] = useState('');
  const [fits, setFits] = useState('');
  const rnd = question.issue_type === 'RND_FEASIBILITY';
  const scheduled = feasibility === 'FEASIBLE_WITH_SCHEDULE';
  const body = {
    version: question.version, reply_text: text.trim(), received_at: received && new Date(received).toISOString(), source_ref: trimmed(source),
    ...(rnd ? {
      rnd_feasibility: feasibility as Question['rnd_feasibility'],
      earliest_available_on: scheduled ? earliest || null : null,
      schedule_fits: scheduled && fits ? fits === 'yes' : null,
    } : {}),
  };
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const reply = useMutation({
    mutationFn: () => replyQuestion(question.question_id, body, key),
    onSuccess: result => { nextKey(); onSaved(!!result.data.created_proposal); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  const ready = body.reply_text !== '' && received !== '' && (!rnd || (feasibility !== '' && (!scheduled || fits !== '')));
  const note = rnd ? '개발 가능성과 일정을 기록합니다. 저장 뒤 연결된 요구에서 대응 방안을 검토하세요.' : question.target === 'INTERNAL' ? '회신을 저장한 뒤 근거를 확인하고 판정 변경 여부를 검토하세요. 판정은 자동으로 바뀌지 않습니다.' : '변경 제안이 생기면 근거를 확인하고 승인 여부를 정하세요. 승인 전에는 요구가 바뀌지 않습니다.';
  return <div style={{ margin: '8px 14px 0' }}><KvBox title="회신 기록" note={note}>
    <form onSubmit={event => { event.preventDefault(); if (ready) reply.mutate(); }}>
      <KvField label="질문">{question.question_text}</KvField>
      <div className="kv-field"><label htmlFor="reply-text" className="req">회신 내용</label>
        <textarea id="reply-text" rows={3} maxLength={20000} value={text} onChange={event => setText(event.target.value)} /></div>
      <div className="kv-field"><label htmlFor="reply-received" className="req">받은 일시</label>
        <input id="reply-received" type="datetime-local" value={received} onChange={event => setReceived(event.target.value)} /></div>
      {rnd && <>
        <div className="kv-field"><label htmlFor="reply-feasibility" className="req">개발 가능성</label>
          <select id="reply-feasibility" value={feasibility} onChange={event => setFeasibility(event.target.value)}>
            <option value="">선택</option>
            {Object.entries(FEASIBILITY_LABEL).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select></div>
        {feasibility === 'UNDETERMINED' && <p className="kv-sub" style={{ margin: '6px 0 0' }}>판단 불가로 기록하면 R&D 확인은 대기로 남습니다</p>}
        {scheduled && <>
          <div className="kv-field"><label htmlFor="reply-earliest">가능 시점</label>
            <input id="reply-earliest" type="date" value={earliest} onChange={event => setEarliest(event.target.value)} /></div>
          <div className="kv-field"><label htmlFor="reply-fits" className="req">납기 부합</label>
            <select id="reply-fits" value={fits} onChange={event => setFits(event.target.value)}>
              <option value="">선택</option>
              <option value="yes">맞음</option>
              <option value="no">안 맞음</option>
            </select></div>
        </>}
      </>}
      <div className="kv-field"><label htmlFor="reply-source">회신 출처</label>
        <input id="reply-source" type="text" placeholder="메일 제목·받은 자료 이름·회의 기록 등" maxLength={512} value={source} onChange={event => setSource(event.target.value)} /></div>
      <div className="kv-actions">
        <button type="button" className="kb" onClick={onCancel}>취소</button>
        <button type="submit" className="kb primary" disabled={!ready || reply.isPending}>회신 기록</button>
      </div>
      {reply.isError && <Failure error={reply.error} onReload={onReload} messages={REPLY_CONFLICT} />}
    </form>
  </KvBox></div>;
}
