import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { addRfpReviewDepartment, listQuestions, type RequirementDetail } from '../api/rfp';
import { ErrorState } from '../components/states';
import { QuestionForm } from './QuestionForms';
import { FEASIBILITY_LABEL, RND_CHECK_LABEL, label } from './labels';
import './DevelopmentReviewFlow.css';

export function DevelopmentReviewFlow({ projectId, actorId, requirement, requirementId, requirementVersion, canRequest, reviewDepartments, partnerCount, onSaved, projectVersion, canEditProject, onProjectSaved, expanded, onExpandedChange, taskReview = false }: {
  projectId: string; actorId: string; requirement?: RequirementDetail; requirementId: string; requirementVersion: number;
  taskReview?: boolean; expanded?: boolean; onExpandedChange?: (value: boolean) => void; canRequest: boolean; projectVersion?: number; canEditProject?: boolean; onProjectSaved?: () => void; reviewDepartments?: string[]; partnerCount?: number; onSaved: () => void;
}) {
  const [writing, setWriting] = useState(false);
  const [saved, setSaved] = useState(false);
  const questions = useQuery({ queryKey: ['rfp-development-questions', getApiMode(), actorId, projectId, requirementId, requirementVersion], queryFn: () => listQuestions(projectId, undefined, { useMockFallback: false }), retry: false, refetchOnWindowFocus: false });
  const current = requirement?.requirement_id === requirementId && requirement.version === requirementVersion ? requirement : undefined;
  const relevant = questions.isSuccess ? questions.data.data.items.filter(q => q.requirement_id === requirementId && q.target === 'INTERNAL' && q.issue_type === 'RND_FEASIBILITY') : [];
  const latest = relevant.find(q => q.question_id === current?.judgement.rnd_question_id) ?? [...relevant].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const open = relevant.some(q => q.status === 'OPEN' || q.status === 'DRAFT');
  const status = current?.judgement.rnd_check;
  const waiting = status === 'PENDING';
  const inconsistent = latest?.rnd_feasibility === 'UNDETERMINED' && status !== 'PENDING' && status !== 'NOT_REQUIRED';
  const feasible = status === 'CONFIRMED_FEASIBLE' && !waiting;
  const alternative = status === 'CONFIRMED_NOT_FEASIBLE' || status === 'CONFIRMED_SCHEDULE_MISMATCH' || status === 'WAIVED';
  const stage = !current || !questions.isSuccess || status === undefined ? -1 : feasible || alternative ? 2 : waiting || open ? 1 : 0;
  const outcome = feasible ? '자사 개발 검토' : alternative ? partnerCount === undefined ? '협력사 근거 확인 필요' : partnerCount > 0 ? '협력사 대응 검토' : '미해결·추가 검토' : '대응 방안 검토';
  const ready = !!current && questions.isSuccess && !questions.isFetching && canRequest && status !== undefined && reviewDepartments?.includes('RND_FW') === true && !open && !saved && !inconsistent;
  const summary = !current ? '상태 확인 중' : status === 'NOT_REQUIRED' ? '별도 개발 확인 조건 없음' : status ? label(RND_CHECK_LABEL, status) : '개발 상태 미제공';
  return <section className="development-review">
    {questions.isError && <ErrorState error={questions.error} onRetry={() => questions.refetch()} />}
    {inconsistent && <p role="alert">요구의 개발 상태와 최근 질문의 판단 불가 회신이 다릅니다. 최신 상세를 확인하세요.</p>}
    <details className="response-card response-disclosure" open={expanded} onToggle={e => onExpandedChange?.(e.currentTarget.open)}><summary><b>{taskReview ? '수행 가능성·일정 검토' : '기술·개발 검토'}</b><span>{summary}{latest?.rnd_feasibility ? ` · 최근 회신 ${label(FEASIBILITY_LABEL, latest.rnd_feasibility)}` : ''}</span></summary><div className="response-disclosure-body">
    <ol className="development-stages" aria-label="개발 검토 단계">{['개발팀 검토 요청', '개발 가능성·일정 판정', outcome].map((text, index) => <li key={text} aria-current={stage === index ? 'step' : undefined}>{text}</li>)}</ol>
    <p>{!current ? '현재 요구 판본의 개발 검토 상태를 확인 중입니다.' : status === 'NOT_REQUIRED' && questions.isSuccess && !relevant.length ? '개발 검토 요청 전 · 현재 별도 개발 확인 조건 없음' : status ? label(RND_CHECK_LABEL, status) : '개발 검토 상태가 제공되지 않았습니다.'}</p>
    {questions.isPending && <p>개발 검토 요청 기록 조회 중</p>}
    {questions.isSuccess && !relevant.length && <p>이 요구의 개발 검토 요청 기록이 없습니다.</p>}
    {latest && <div className="development-record"><p>최근 요청: {latest.question_text}</p><p>요청 일시: {latest.created_at} · {({ DRAFT: '초안', OPEN: '회신 대기', REPLIED: '회신 기록됨' })[latest.status]}</p>{latest.rnd_feasibility && <p>회신 개발 가능성: {label(FEASIBILITY_LABEL, latest.rnd_feasibility)}</p>}{latest.reply_received_at && <p>회신 일시: {latest.reply_received_at}</p>}<small>질문 기록은 이 요구에 연결됩니다. 요구 판본별 회신 내용은 상세에서 확인하세요.</small></div>}
    {waiting && <p>개발 가능성과 일정 확인을 기다립니다. 판단 불가 회신은 대기로 남습니다.</p>}
    {feasible && <p>개발 가능 회신이 기록되었습니다. 실제 대응 경로는 담당자가 근거와 조건을 검토해 선택합니다.</p>}
    {alternative && <p>{status === 'WAIVED' ? '개발 확인 면제가 기록되었습니다.' : '자사 개발 불가 또는 일정 불일치가 기록되었습니다.'} 협력사 근거와 남은 조건을 확인하세요.</p>}
    <p>{partnerCount == null ? '현재 요구·프로필 판본의 협력사 근거를 확인 중입니다.' : partnerCount > 0 ? `제외되지 않은 협력사 검토 후보 ${partnerCount}곳 · 공급·납기·전체 장비 충족을 보장하지 않습니다.` : alternative ? '현 자료에 대응 근거 없음 · 추가 검토가 필요합니다.' : '협력사 검토 후보가 없습니다. 자사 개발 여부를 단정하지 않습니다.'}</p>
    <p className="development-delivery">앱에 검토 요청을 기록합니다. 실제 이메일 전달 여부는 이 화면에서 확인하지 않습니다.</p>
    {!reviewDepartments?.includes('RND_FW') && <p>프로젝트 개발 검토 부서가 확인되어야 요청을 작성할 수 있습니다.</p>}
    {!reviewDepartments?.includes('RND_FW') && <ReviewDepartmentSetup key={`${projectId}:${projectVersion}`} projectId={projectId} actorId={actorId} version={projectVersion} departments={reviewDepartments} canEdit={canEditProject === true} onSaved={onProjectSaved ?? (() => {})} />}
    {open && <p>진행 중인 개발 검토 요청이 있습니다. 상세에서 회신 기록을 확인하세요.</p>}
    {writing && ready && current ? <QuestionForm key={`${requirementId}:${requirementVersion}`} projectId={projectId} requirement={current} departments={['RND_FW']} initialTarget="RND_FW" actorId={actorId} useMockFallback={false} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setSaved(true); void questions.refetch(); onSaved(); }} /> : <button className="kb" type="button" disabled={!ready} onClick={() => setWriting(true)}>{taskReview ? '수행 가능성 검토 요청 작성' : '개발 검토 요청 작성'}</button>}
    <Link className="development-detail" to={`/tft/projects/${projectId}/requirements/${requirementId}`}>요구 상세·회신 기록</Link>
  </div></details></section>;
}

function ReviewDepartmentSetup({ projectId, actorId, version, departments, canEdit, onSaved }: {
  projectId: string; actorId: string; version?: number; departments?: string[]; canEdit: boolean; onSaved: () => void;
}) {
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const update = useMutation({
    mutationFn: async () => {
      if (!canEdit || version == null || departments == null || !confirmed) throw new ApiError(403, '프로젝트 권한과 판본을 다시 확인하세요.');
      const result = await addRfpReviewDepartment(projectId, version, departments, actorId);
      if (result.data.id !== projectId || result.data.version <= version || !result.data.review_departments.includes('RND_FW')) throw new ApiError(409, '저장 응답을 확인할 수 없습니다. 프로젝트를 새로 조회하세요.');
      return result;
    }, retry: false,
    onSuccess: () => { setConfirming(false); onSaved(); },
    onError: error => {
      if (error instanceof ApiError && (error.code === 'SESSION_CHANGED' || error.status === 401)) {
        client.setQueryData(['me'], null);
        void client.invalidateQueries({ queryKey: ['me'] });
      }
    },
  });
  const ready = canEdit && version != null && departments != null && !update.isPending && !update.isError && !update.isSuccess;
  return <div className="development-department-setup">
    <button className="kb" type="button" disabled={!ready} onClick={() => setConfirming(true)}>R&D 검토 부서 추가</button>
    {confirming && <form onSubmit={event => { event.preventDefault(); if (ready && confirmed) update.mutate(); }}>
      <p>기존 검토 부서를 유지하고 R&D 부서를 추가합니다. 이 부서 소속 담당자에게 이 프로젝트와 RFP 기록의 조회 권한이 생깁니다.</p>
      <label><input type="checkbox" checked={confirmed} disabled={!ready} onChange={event => setConfirmed(event.target.checked)} />프로젝트 조회 권한 부여를 확인했습니다.</label>
      <div><button className="kb" type="button" disabled={update.isPending} onClick={() => { setConfirming(false); setConfirmed(false); }}>취소</button><button className="kb primary" type="submit" disabled={!ready || !confirmed}>확인하고 검토 부서 추가</button></div>
    </form>}
    {update.isError && <><ErrorState error={update.error} /><p>자동으로 다시 저장하지 않습니다. 최신 프로젝트를 조회한 뒤 다시 확인하세요.</p><button className="kb" type="button" onClick={onSaved}>프로젝트 최신 조회</button></>}
    {update.isSuccess && <p role="status">R&D 검토 부서를 추가했습니다. 프로젝트를 다시 확인 중입니다.</p>}
    {!canEdit && <p>검토 부서 추가는 프로젝트 담당자·책임자만 할 수 있습니다.</p>}
  </div>;
}
