import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { createQuestion, replyQuestion, type Question, type RequirementDetail } from '../api/rfp';
import { KvBox, KvField } from '../ls-shell/kv';
import { ISSUE_LABEL } from './labels';
import { Failure, settled, useAttemptKey } from './proposal-shared';

// 고객 질문에 쓰는 유형. 내부 자문 유형은 INTERNAL 전용이라 서버가 거절한다.
const CUSTOMER_ISSUES = ['EMPTY_VALUE', 'CONFLICT', 'SCOPE_UNCLEAR', 'CONDITION_MISSING'];
const clauseOf = ({ origin }: RequirementDetail) => [origin.clause_no, origin.page != null ? `p.${origin.page}` : ''].filter(Boolean).join(', ');
const trimmed = (text: string) => text.trim() || null;

// 고객 질문 작성(TF_K_Req_Question). 가정값은 충족 판정에 쓰지 않는다.
// ponytail: 승인자를 고를 사용자 목록 API가 없어 승인자는 비우고 초안 표시는 끈다. 승인 없는 가정이 고객 초안에 나가면 안 된다.
export function QuestionForm({ projectId, requirement, onCancel, onSaved }: {
  projectId: string;
  requirement: RequirementDetail;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [issue, setIssue] = useState(CUSTOMER_ISSUES[0]);
  const [text, setText] = useState('');
  const [assumed, setAssumed] = useState('');
  const [standard, setStandard] = useState('');
  const [clause, setClause] = useState('');
  const { clause_id, page } = requirement.origin;
  const body = {
    requirement_id: requirement.requirement_id, origin: { clause_id, page }, issue_type: issue as Question['issue_type'], question_text: text.trim(),
    target: 'CUSTOMER' as const, target_dept: null,
    internal_assumption: assumed.trim()
      ? { value: assumed.trim(), basis_standard: trimmed(standard), basis_clause: trimmed(clause), approver_user_id: null, shown_in_draft: false }
      : null,
  };
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const create = useMutation({
    mutationFn: () => createQuestion(projectId, body, key),
    onSuccess: () => { nextKey(); onSaved(); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  return <div style={{ margin: '8px 14px 0' }}><KvBox title={`고객 질문 작성, ${requirement.code}`}>
    <form onSubmit={event => { event.preventDefault(); if (body.question_text) create.mutate(); }}>
      <KvField label="원문">{clauseOf(requirement) || '조항 없음'}</KvField>
      <div className="kv-field"><label htmlFor="question-issue">유형</label>
        <select id="question-issue" value={issue} onChange={event => setIssue(event.target.value)}>
          {CUSTOMER_ISSUES.map(code => <option key={code} value={code}>{ISSUE_LABEL[code]}</option>)}
        </select></div>
      <div className="kv-field"><label htmlFor="question-text" className="req">질문</label>
        <textarea id="question-text" rows={3} maxLength={4000} value={text} onChange={event => setText(event.target.value)} /></div>
      <div className="kv-field"><label htmlFor="question-assumed">가정값</label>
        <input id="question-assumed" type="text" placeholder="회신 전 가정값, 선택. 충족 판정에는 쓰지 않음" value={assumed} onChange={event => setAssumed(event.target.value)} /></div>
      {assumed.trim() && <>
        <div className="kv-field"><label htmlFor="question-standard">근거 규격</label><input id="question-standard" type="text" value={standard} onChange={event => setStandard(event.target.value)} /></div>
        <div className="kv-field"><label htmlFor="question-clause">근거 조항</label><input id="question-clause" type="text" value={clause} onChange={event => setClause(event.target.value)} /></div>
      </>}
      <div className="kv-actions">
        <button type="button" className="kb" onClick={onCancel}>취소</button>
        <button type="submit" className="kb primary" disabled={!body.question_text || create.isPending}>질문 저장</button>
      </div>
      {create.isError && <Failure error={create.error} onReload={onSaved} messages={{}} />}
    </form>
  </KvBox></div>;
}

const REPLY_CONFLICT = { TFT_VERSION_CONFLICT: '이 질문은 이미 회신됐거나 바뀌었습니다. 다시 불러와 확인하세요. 입력한 내용은 남습니다.' };

// 회신 기록(TF_K_Req_Reply). 서버는 미확인 항목이 줄 때만 변경 제안을 만든다.
export function ReplyForm({ question, onCancel, onSaved, onReload }: {
  question: Question;
  onCancel: () => void;
  onSaved: (proposed: boolean) => void;
  onReload: () => void;
}) {
  const [text, setText] = useState('');
  const [received, setReceived] = useState('');
  const [source, setSource] = useState('');
  const body = { version: question.version, reply_text: text.trim(), received_at: received && new Date(received).toISOString(), source_ref: trimmed(source) };
  const [key, nextKey] = useAttemptKey(JSON.stringify(body));
  const reply = useMutation({
    mutationFn: () => replyQuestion(question.question_id, body, key),
    onSuccess: result => { nextKey(); onSaved(!!result.data.created_proposal); },
    onError: error => { if (settled(error)) nextKey(); },
  });
  const ready = body.reply_text !== '' && received !== '';
  return <div style={{ margin: '8px 14px 0' }}><KvBox title="회신 기록" note="미확인 항목이 줄면 변경 제안이 생깁니다. 승인 전에는 요구가 바뀌지 않음">
    <form onSubmit={event => { event.preventDefault(); if (ready) reply.mutate(); }}>
      <KvField label="질문">{question.question_text}</KvField>
      <div className="kv-field"><label htmlFor="reply-text" className="req">회신 내용</label>
        <textarea id="reply-text" rows={3} maxLength={20000} value={text} onChange={event => setText(event.target.value)} /></div>
      <div className="kv-field"><label htmlFor="reply-received" className="req">받은 일시</label>
        <input id="reply-received" type="datetime-local" value={received} onChange={event => setReceived(event.target.value)} /></div>
      <div className="kv-field"><label htmlFor="reply-source">참조</label>
        <input id="reply-source" type="text" maxLength={512} value={source} onChange={event => setSource(event.target.value)} /></div>
      <div className="kv-actions">
        <button type="button" className="kb" onClick={onCancel}>취소</button>
        <button type="submit" className="kb primary" disabled={!ready || reply.isPending}>회신 기록</button>
      </div>
      {reply.isError && <Failure error={reply.error} onReload={onReload} messages={REPLY_CONFLICT} />}
    </form>
  </KvBox></div>;
}
