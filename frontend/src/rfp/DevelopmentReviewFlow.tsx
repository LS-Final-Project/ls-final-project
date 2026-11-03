import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { addRfpReviewDepartment, listQuestions, type RequirementDetail } from '../api/rfp';
import { ErrorState } from '../components/states';
import { QuestionForm } from './QuestionForms';
import { FEASIBILITY_LABEL, RND_CHECK_LABEL, label, requirementNo } from './labels';
import './DevelopmentReviewFlow.css';

export function DevelopmentReviewFlow({ projectId, actorId, requirement, requirementId, requirementVersion, canRequest, reviewDepartments, partnerCount, onSaved, projectVersion, canEditProject, onProjectSaved, expanded, onExpandedChange, embedded = false }: {
  projectId: string; actorId: string; requirement?: RequirementDetail; requirementId: string; requirementVersion: number;
  embedded?: boolean; taskReview?: boolean; expanded?: boolean; onExpandedChange?: (value: boolean) => void; canRequest: boolean; projectVersion?: number; canEditProject?: boolean; onProjectSaved?: () => void; reviewDepartments?: string[]; partnerCount?: number; onSaved: () => void;
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
  const ready = !!current && questions.isSuccess && !questions.isFetching && canRequest && status !== undefined && reviewDepartments?.includes('RND_FW') === true && !open && !saved && !inconsistent;
  const summary = !current ? '상태 확인 중' : status === 'NOT_REQUIRED' ? '별도 개발 확인 조건 없음' : status ? label(RND_CHECK_LABEL, status) : '개발 상태 미제공';
  const body = <>
    {questions.isPending && <p role="status">개발팀 질문 기록을 확인 중입니다.</p>}
    {latest && <div className="development-record" aria-label="연결된 개발팀 질문">
      <div className="development-record-state">{({ DRAFT: '질문 초안', OPEN: '회신 대기', REPLIED: '회신 기록됨' })[latest.status]}</div>
      <h5>개발팀에 확인한 질문</h5><p className="development-record-text">{latest.question_text}</p>
      {latest.rnd_feasibility && <p className="development-reply-result">회신 개발 가능성: <strong>{label(FEASIBILITY_LABEL, latest.rnd_feasibility)}</strong></p>}
    </div>}
    {!current ? <p>현재 요구 판본의 개발 검토 상태를 확인 중입니다.</p>
      : questions.isSuccess && !latest && !feasible && !alternative && !waiting && <p className="development-next">자사 개발 가능 여부와 가능한 일정을 개발팀에 확인하세요.</p>}
    {current && (waiting || open) && <p className="development-next">{latest?.rnd_feasibility === 'UNDETERMINED' ? '판단 불가 회신으로 개발 확인이 대기 중입니다. 추가로 확인할 조건을 검토하세요.' : '질문과 회신 기록을 확인하세요. 개발 가능성과 일정 확인이 남아 있습니다.'}</p>}
    {feasible && <p className="development-next">개발 가능 회신이 기록되었습니다. 실제 대응 경로는 담당자가 근거와 조건을 검토해 선택합니다.</p>}
    {alternative && <p className="development-next">{status === 'WAIVED' ? '개발 확인 면제가 기록되었습니다.' : '자사 개발 불가 또는 일정 불일치가 기록되었습니다.'} 협력사 근거와 남은 조건을 확인하세요.</p>}
    {current && status !== undefined && (latest || waiting || feasible || alternative) && <p className="development-gate">현재 요구의 개발 확인: <strong>{label(RND_CHECK_LABEL, status)}</strong> · 이 기록만으로 대응 경로나 제안이 확정되지 않습니다.</p>}
    <div className="development-primary-actions">
      <button className="kb primary" type="button" disabled={!ready || writing} onClick={() => setWriting(true)}>개발팀에 확인할 질문 작성</button>
      <Link className="development-detail" to={`/tft/projects/${projectId}/requirements/${requirementId}`}>요구 상세·회신 기록</Link>
    </div>
    {writing && current && <QuestionForm key={`${requirementId}:${requirementVersion}`} projectId={projectId} requirement={current} departments={['RND_FW']} initialTarget="RND_FW" initialText={`요구 ${requirementNo(current.code)}에 대해 자사 개발 가능 여부와 가능한 일정, 필요한 조건을 확인해 주세요.`} disabled={!ready} actorId={actorId} useMockFallback={false} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setSaved(true); void questions.refetch(); onSaved(); }} />}
    {!reviewDepartments?.includes('RND_FW') && <div className="development-access"><p>질문을 작성하려면 프로젝트에 개발 검토 부서를 먼저 등록하세요.</p><ReviewDepartmentSetup key={`${projectId}:${projectVersion}`} projectId={projectId} actorId={actorId} version={projectVersion} departments={reviewDepartments} canEdit={canEditProject === true} onSaved={onProjectSaved ?? (() => {})} /></div>}
    <details className="development-support"><summary>검토 근거와 기록 정보</summary>
      {questions.isSuccess && !relevant.length && <p>이 요구의 개발 검토 요청 기록이 없습니다.</p>}
      {latest && <><p>요청 일시: {latest.created_at}</p>{latest.reply_received_at && <p>회신 일시: {latest.reply_received_at}</p>}<p>질문 기록은 이 요구에 연결됩니다. 이 화면에서는 회신의 개발 가능성 결과와 회신 일시를 확인할 수 있습니다.</p></>}
      {current && status === 'NOT_REQUIRED' && <p>현재 요구에 별도 개발 확인 조건이 기록되어 있지 않습니다. 개발 가능하다는 뜻은 아닙니다.</p>}
      <p>{partnerCount == null ? '현재 요구·프로필 판본의 협력사 근거를 확인 중입니다.' : partnerCount > 0 ? `제외되지 않은 협력사 검토 후보 ${partnerCount}곳 · 공급·납기·전체 장비 충족을 보장하지 않습니다.` : alternative ? '현 자료에 대응 근거 없음 · 추가 검토가 필요합니다.' : '협력사 검토 후보가 없습니다. 자사 개발 여부를 단정하지 않습니다.'}</p>
      <p className="development-delivery">이 기능은 개발팀의 가능성·일정 확인을 기록합니다. 다른 부서의 수행 가능성을 판정하지 않습니다. 실제 이메일 전달 여부는 이 화면에서 확인하지 않습니다.</p>
    </details>
  </>;
  return <section className="development-review">
    {questions.isError && <ErrorState error={questions.error} onRetry={() => questions.refetch()} />}
    {inconsistent && <p role="alert">요구의 개발 상태와 최근 질문의 판단 불가 회신이 다릅니다. 최신 상세를 확인하세요.</p>}

    {embedded ? <><header><h4>개발팀 가능성·일정 확인</h4><small>{summary}{latest?.rnd_feasibility ? ` · 최근 회신 ${label(FEASIBILITY_LABEL, latest.rnd_feasibility)}` : ''}</small></header><div className="response-disclosure-body">{body}</div></> : <details className="response-card response-disclosure" open={expanded} onToggle={e => onExpandedChange?.(e.currentTarget.open)}><summary><b>개발팀 가능성·일정 확인</b><span>{summary}</span></summary><div className="response-disclosure-body">{body}</div></details>}
  </section>;
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
