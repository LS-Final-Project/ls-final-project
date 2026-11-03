import { useQuery } from '@tanstack/react-query';
import { useRef } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getComparison } from '../api/rfp';
import { getSupplementRequirement, getSupplementOptions, listSupplementCandidates, type RequirementSummary } from '../api/rfp-supplement';
import { ErrorState, Loading } from '../components/states';
import { SelectionEditor, reasonLabel } from './RfpSupplement';
import { BLOCKED_LABEL, PATH_LABEL, RND_CHECK_LABEL, label } from './labels';
import './ResponseDecision.css';
import { DevelopmentReviewFlow } from './DevelopmentReviewFlow';

const noop = () => {};
const resultLabel = { MEETS: '충족', DIFFERS: '사양 차이', NOT_COMPARABLE: '비교 불가', NO_DATA: '자료 없음' };
const gradeLabel = { MANDATORY_CONFIRMED: '필수 기술조건 확인', REVIEW: '검토', REFERENCE_ONLY: '확인용', EXCLUDED: '제외' };
export function ResponseDecision({ projectId, actorId, requirement, canSelect, onSaved, reviewDepartments, projectVersion, canEditProject, onProjectSaved }: {
  projectId: string; actorId: string; requirement: RequirementSummary; canSelect: boolean; onSaved: () => void; reviewDepartments?: string[]; projectVersion?: number; canEditProject?: boolean; onProjectSaved?: () => void;
}) {
  const mode = getApiMode();
  const { requirement_id: id, version } = requirement;
  const prefix = ['rfp-response', mode, actorId, projectId, id, version];
  const attempts = useRef(new Map<string, string>());
  const detail = useQuery({ queryKey: ['rfp-response-detail', mode, actorId, projectId, id, version], queryFn: () => getSupplementRequirement(id), retry: false, refetchOnWindowFocus: false });
  const comparison = useQuery({ queryKey: [...prefix, 'comparison'], queryFn: () => getComparison(id, { useMockFallback: false }), retry: false, refetchOnWindowFocus: false });
  const options = useQuery({ queryKey: [...prefix, 'options'], queryFn: () => getSupplementOptions(id), retry: false, refetchOnWindowFocus: false });
  const candidates = useQuery({ queryKey: [...prefix, 'candidates'], queryFn: () => listSupplementCandidates(projectId, id), retry: false, refetchOnWindowFocus: false });
  const currentDetail = detail.isSuccess && detail.data?.data.requirement_id === id && detail.data.data.version === version ? detail.data.data : undefined;
  const currentComparison = comparison.isSuccess && comparison.data?.data.requirement_id === id && comparison.data.data.requirement_version === version ? comparison.data.data : undefined;
  const currentOptions = options.isSuccess && options.data?.data.requirement_id === id && options.data.data.version === version ? options.data.data : undefined;
  const candidateRows = candidates.data?.data.items ?? [];
  const currentGrade = (c: typeof candidateRows[number]) => c.grades.filter(g => g.requirement_id === id && g.requirement_version === version && g.profile_version === c.profile_version);
  const candidateVersionsMatch = candidateRows.every(c => currentGrade(c).length === 1);
  const optionCodesKnown = currentOptions?.options.every(o => o.candidate_codes.every(code => candidateRows.some(c => c.candidate_code === code))) ?? false;
  const selectionReady = !!currentDetail && !!currentOptions && candidates.isSuccess && candidateVersionsMatch && optionCodesKnown;
  const refresh = () => { void detail.refetch(); void comparison.refetch(); void options.refetch(); void candidates.refetch(); };
  return <div className="rfp-response-decision rfp-supplement">
    <section className="response-card"><h3>1. 자사 대응 근거</h3>
      {comparison.isPending ? <Loading label="자사 비교 조회 중" /> : comparison.isError ? <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} /> : !currentComparison ? <p role="alert">현재 요구 판본의 자사 비교 결과가 아닙니다. 최신 조회가 필요합니다.</p> : <>
        <div className="response-own-list">{currentComparison.candidates.map(c => <article className="response-evidence" key={`${c.product_code}:${c.variant}`}><b>{c.product_code} {c.variant}</b><p>{[c.attribute_match.our_value, c.attribute_match.our_unit].filter(Boolean).join(' ') || '자료 없음'} · {resultLabel[c.attribute_match.result]}</p><details><summary>비교 근거</summary>{c.attribute_match.evidence.map((e, index) => <p key={index}>근거: {[e.document, e.version, e.clause, e.applicability, e.limits].filter(Boolean).join(' · ')}</p>)}{!c.attribute_match.evidence.length && <p>근거 미기재</p>}</details></article>)}</div>
        {!currentComparison.candidates.length && <p>{currentComparison.search_trace.search_failed ? '자사 후보 검색 실패' : '현재 검색 범위의 자사 후보 없음'}</p>}
        <small>요구 v{currentComparison.requirement_version} · 비교 v{currentComparison.version} · {currentComparison.known_at}</small>
      </>}
      <p>후보나 자료가 없다는 사실만으로 외주 또는 장비 전체의 충족을 판단하지 않습니다.</p>
    </section>
    <DevelopmentReviewFlow key={`${id}:${version}`} projectId={projectId} actorId={actorId} requirement={currentDetail} requirementId={id} requirementVersion={version} canRequest={canSelect && !detail.isFetching} reviewDepartments={reviewDepartments} projectVersion={projectVersion} canEditProject={canEditProject} onProjectSaved={onProjectSaved} partnerCount={candidates.isSuccess && candidateVersionsMatch ? candidateRows.filter(c => currentGrade(c).some(g => g.grade !== 'EXCLUDED')).length : undefined} onSaved={onSaved} />
    <section className="response-card"><h3>2. 협력사 보완 후보</h3>
      {candidates.isPending && <Loading label="협력사 후보 조회 중" />}{candidates.isError && <ErrorState error={candidates.error} onRetry={() => candidates.refetch()} />}
      {candidates.isSuccess && <><p>가상 협력사 · 이 요구와 프로필 판본의 저장 등급만 표시합니다.</p><div className="response-partner-list">{candidateRows.map(c => <article className="response-evidence" key={c.candidate_code}><b>{c.candidate_code} · {c.display_name}</b><p>프로필 v{c.profile_version} · 인증 {c.certifications.join(' · ') || '미기재'}</p>{currentGrade(c).length === 1 ? currentGrade(c).map(g => <div key={`${g.requirement_id}:${g.profile_version}`}><strong>{gradeLabel[g.grade]}</strong><p>{g.reasons.map(reasonLabel).join(' · ') || '이유 미기재'}</p><p>제외 사유: {g.exclusion_reasons.map(reasonLabel).join(' · ') || '없음'}</p><p>미충족: {g.mandatory_check.failed.map(reasonLabel).join(' · ') || '없음'} · 미확인: {g.mandatory_check.unknown.map(reasonLabel).join(' · ') || '없음'}</p><small>요구 v{g.requirement_version} · 프로필 v{g.profile_version} · 등급 v{g.version ?? '미기재'}</small></div>) : <p role="alert">현재 요구·프로필 판본과 연결된 등급을 확인할 수 없습니다.</p>}</article>)}</div>{!candidateRows.length && <p>저장된 후보가 없습니다.</p>}</>}
    </section>
    <section className="response-card"><h3>3. 미해결·확인 사항</h3>
      {detail.isPending && <Loading label="요구 상세 조회 중" />}{detail.isError && <ErrorState error={detail.error} onRetry={() => detail.refetch()} />}
      {currentDetail ? <><details><summary>요구 인용문 확인</summary><blockquote>{currentDetail.origin.quote}</blockquote></details><p>{currentDetail.judgement.reason}</p><p>{[...currentDetail.spec.undetermined_items, ...currentDetail.judgement.unresolved].join(' · ') || '기록된 미해결 항목 없음'}</p>{(currentDetail.needs_review || currentDetail.needs_rereview) && <p>원문 확인·재검토가 필요합니다.</p>}</> : detail.isSuccess && <p role="alert">요구 상세의 판본이 일치하지 않습니다.</p>}
      {options.isError && <ErrorState error={options.error} onRetry={() => options.refetch()} />}{currentOptions?.options.map(o => <div className="response-gate" key={o.path}><b>{label(PATH_LABEL, o.path)} · {o.allowed ? '허용' : '차단'}</b><p>{o.conditions_to_confirm.join(' · ') || '추가 조건 미기재'} · {label(RND_CHECK_LABEL, o.gate.status)}</p>{!o.allowed && <p>{label(BLOCKED_LABEL, o.blocked_reason) || '차단 사유 확인 필요'}</p>}</div>)}
      <Link to={`/tft/projects/${projectId}/requirements/${id}`}>상세 검토·변경 제안·질문</Link>
    </section>
    <section className="response-card"><h3>4. 사람의 대응 경로 선택</h3><button className="kb" type="button" onClick={refresh} disabled={detail.isFetching || comparison.isFetching || options.isFetching || candidates.isFetching}>대응 근거 최신 조회</button>
      {!canSelect && <p>조회 권한으로 열었습니다. 대응 경로 저장은 담당자만 가능합니다.</p>}
      {options.isPending && <Loading label="대응 경로 조회 중" />}
      {selectionReady && currentOptions ? <SelectionEditor key={`${id}:${version}:${options.dataUpdatedAt}:${candidates.dataUpdatedAt}`} compact actorId={actorId} attempts={attempts.current} onSaveReady={noop} options={currentOptions} candidates={candidateRows} canSelect={canSelect && !detail.isFetching && !options.isFetching && !candidates.isFetching} onSaved={onSaved} /> : !options.isPending && !candidates.isPending && <p role="alert">요구·경로·후보의 판본과 연결을 확인한 후 선택할 수 있습니다.</p>}
    </section>
  </div>;
}
