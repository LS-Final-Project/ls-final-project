import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listQuestions, type RequirementDetail } from '../api/rfp';
import { formatDateTime } from '../components/tft';
import { ErrorState, Loading } from '../components/states';
import { QuestionForm } from './QuestionForms';
import { DepartmentPicker } from './GroupActions';
import { DEPARTMENT_LABEL, ISSUE_LABEL, QUESTION_STATUS_LABEL, attributeName, label } from './labels';

export function GuidedRequirementReview({ projectId, actorId, requirement, departments, canWrite, customerValueMissing, commercial, designated, scope = false, onSaved }: {
  projectId: string; actorId: string; requirement: RequirementDetail; departments?: string[]; canWrite: boolean;
  // scope: 과업의 수행 주체·범위를 발주처에 묻는다.
  customerValueMissing: boolean; commercial: boolean; designated: boolean; scope?: boolean; onSaved: () => void;
}) {
  const [writing, setWriting] = useState(false);
  const [notice, setNotice] = useState('');
  const questions = useQuery({ queryKey: ['rfp-guided-questions', getApiMode(), actorId, projectId, requirement.requirement_id, requirement.version], queryFn: () => listQuestions(projectId, undefined, { useMockFallback: false }), retry: false, refetchOnWindowFocus: false });
  const records = questions.isSuccess ? questions.data.data.items.filter(q => q.requirement_id === requirement.requirement_id).sort((a,b) => b.created_at.localeCompare(a.created_at)) : [];
  const internal = !customerValueMissing && !scope;
  const issue = scope ? 'SCOPE_UNCLEAR' : customerValueMissing ? 'EMPTY_VALUE' : commercial || designated ? 'PURCHASE_TERMS' : 'TECH_ADVICE';
  const subject = attributeName(requirement.spec.attribute_key, requirement.spec.attribute) || '해당 요구';
  const prompt = scope ? `${requirement.spec.task || subject}의 수행 범위와 외부 위탁 허용 여부를 확인해 주세요.`
    : customerValueMissing ? `${subject}의 고객 요구값과 적용 조건을 확인해 주세요.`
    : commercial ? `${subject}의 고객 요구 ${[requirement.spec.value, requirement.spec.unit].filter(Boolean).join(' ')}에 맞출 수 있는지, 추가 비용과 승인 조건을 확인해 주세요.`
    : designated ? `${subject}의 지정 모델 조달 가능 여부와 필요한 고객 확인 사항을 검토해 주세요.`
    : `${subject}에 대응할 수 있는지, 필요한 적용 조건과 추가 자료를 확인해 주세요.`;
  const allowed = canWrite && (!internal || !!departments?.length);
  return <section className="response-guided-review" aria-label="확인 질문과 기록">
    <h4>{scope ? '발주처에 수행 범위 확인' : customerValueMissing ? '고객에게 요구값 확인' : commercial ? '영업·보증 조건 확인' : designated ? '지정품 조달 조건 확인' : '미확인 조건 검토'}</h4>
    <p>{scope ? '수행 주체나 외부 위탁 허용 여부가 원문에 없으면 발주처에 질문으로 확인하세요.' : customerValueMissing ? '요구값이 비어 있어 비교할 수 없습니다. 고객에게 값과 적용 조건을 확인하세요.' : commercial ? '자사 조건을 고객 요구에 맞출 수 있는지, 비용·적용 범위·승인 조건을 내부 검토 부서에 확인하세요.' : designated ? '지정 모델의 조달 가능 여부와 고객이 허용하는 범위를 확인하세요.' : '근거가 부족한 부분을 내부 검토 부서에 질문하고 확인 내용을 남기세요.'}</p>
    <div className="response-question-records" aria-label="저장된 질문과 회신 상태">
      {questions.isPending && <Loading label="질문 기록 조회 중" />}
      {questions.isError && <ErrorState error={questions.error} onRetry={() => questions.refetch()} />}
      {questions.isSuccess && !records.length && <p>이 요구에 저장된 질문이 없습니다.</p>}
      {records.map(q => <article key={q.question_id}><strong>{q.question_text}</strong><p>{q.target === 'INTERNAL' ? label(DEPARTMENT_LABEL, q.target_dept) || '내부 부서 미기재' : '고객'} · {label(ISSUE_LABEL, q.issue_type)} · {label(QUESTION_STATUS_LABEL, q.status)}</p><small>기록 일시 {formatDateTime(q.created_at)}{q.reply_received_at ? ` · 회신 기록 일시 ${formatDateTime(q.reply_received_at)}` : ''}</small>{q.status === 'REPLIED' && <p>회신이 기록되었습니다. 내용 확인이 필요합니다.</p>}</article>)}
    </div>
    {notice && <p role="status">{notice}</p>}
    {!writing && <button className="kb primary" type="button" disabled={!allowed} onClick={() => setWriting(true)}>{scope ? '발주처 질문 작성' : customerValueMissing ? '고객 확인 질문 작성' : '검토 질문 작성'}</button>}
    {writing && <QuestionForm projectId={projectId} actorId={actorId} requirement={requirement} departments={departments ?? []} audience={internal ? 'INTERNAL' : 'CUSTOMER'} initialIssue={issue} initialText={prompt} disabled={!allowed} useMockFallback={false} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setNotice('질문을 앱에 기록했습니다.'); void questions.refetch(); onSaved(); }} />}
    {!canWrite && <p>현재 읽기 전용입니다. 질문 작성은 담당자 권한과 최신 요구를 확인한 후 가능합니다.</p>}
    {canWrite && internal && !departments?.length && <DepartmentPicker projectId={projectId} actorId={actorId} value="" onChange={() => {}} />}
    <p className="response-record-note">질문 저장은 앱 내 기록이며 이메일 발송이 아닙니다. 질문 초안 확인과 회신 기록은 <Link to={`/tft/projects/${projectId}/requirements/${requirement.requirement_id}`}>요구 상세의 질문·회신</Link>에서 처리하세요.</p>
  </section>;
}
