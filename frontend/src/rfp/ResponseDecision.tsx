import { useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { getComparison } from '../api/rfp';
import { getSupplementRequirement, getSupplementOptions, listSupplementCandidates, type RequirementSummary } from '../api/rfp-supplement';
import { ErrorState, Loading } from '../components/states';
import { SelectionEditor, reasonLabel } from './RfpSupplement';
import { attributeName, requirementNo, STATUS_LABEL, BLOCKED_LABEL, PATH_LABEL, RND_CHECK_LABEL, label } from './labels';
import './ResponseDecision.css';
import { GuidedRequirementReview } from './GuidedRequirementReview';
import { DevelopmentReviewFlow } from './DevelopmentReviewFlow';

const noop = () => {};
const displayUnit = (unit: string | null | undefined) => unit && ['month', 'months', '개월'].includes(unit.toLowerCase()) ? '개월' : unit;
const displayValue = (value: string | null | undefined, unit: string | null | undefined) => [value, displayUnit(unit)].filter(Boolean).join(' ');
const resultLabel = { MEETS: '값 비교상 충족', DIFFERS: '조건 차이', NOT_COMPARABLE: '비교 불가', NO_DATA: '자료 없음' };
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
  const workspace = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState<string>();
  const [visited, setVisited] = useState<Set<string>>(() => new Set());
  const openWorkspace = (panel: string) => { setVisited(previous => new Set(previous).add(panel)); setExpanded(panel); requestAnimationFrame(() => { const element = workspace.current; if (element && element.getBoundingClientRect().bottom > window.innerHeight) element.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' }); }); };
  const savedPath = currentDetail?.judgement.path;
  const selectionRecord = currentDetail?.links.supplement_summary;
  const hasSavedPath = !!savedPath && savedPath !== 'NONE';
  const reviewNeeded = requirement.needs_review || requirement.needs_rereview || currentDetail?.needs_review || currentDetail?.needs_rereview;
  const statusMismatch = !!currentDetail && currentDetail.judgement.status !== requirement.status;
  const selectionLinkMismatch = !!currentOptions && candidates.isSuccess && !optionCodesKnown;
  const issue = selectionLinkMismatch || statusMismatch || detail.isError || comparison.isError || options.isError || candidates.isError
    || detail.isSuccess && !currentDetail || comparison.isSuccess && !currentComparison
    || options.isSuccess && !currentOptions || candidates.isSuccess && !candidateVersionsMatch;
  const cause = currentOptions?.deficiency_cause;
  const commercial = cause === 'COMMERCIAL';
  const designated = cause === 'DESIGNATED_MODEL' || currentOptions?.options.some(o => o.path === 'CUSTOMER_SPECIFIED_PROCUREMENT');
  const task = cause === 'TASK' || !!currentDetail?.spec.task;
  const developmentGate = currentOptions?.options.some(o => o.gate.requires === 'OWN_DEVELOPMENT_CHECK') === true;
  const recordedDevelopment = !!currentDetail?.judgement.rnd_question_id || ['PENDING', 'CONFIRMED_FEASIBLE', 'CONFIRMED_NOT_FEASIBLE', 'CONFIRMED_SCHEDULE_MISMATCH', 'WAIVED'].includes(currentDetail?.judgement.rnd_check ?? '');
  const showDevelopment = recordedDevelopment || !commercial && !designated && developmentGate;
  const showPartners = currentOptions?.options.some(o => ['EXTERNAL_PRODUCT', 'TASK_SUBCONTRACT', 'JOINT_PROPOSAL'].includes(o.path)) === true;
  const customerValueMissing = !!currentDetail && !task && currentDetail.spec.operator !== 'TEXT' && !currentDetail.spec.value?.trim();
  const guided = !!currentDetail && (customerValueMissing || commercial || designated || !showDevelopment && currentDetail.judgement.status !== 'EVIDENCE_FOUND');
  const activePanel = expanded === 'closed' ? undefined : expanded ?? (guided ? 'questions' : undefined);
  const termsOption = currentOptions?.options.find(o => o.path === 'OWN_TERMS_REVIEW');
  const next = issue ? { text: '최신 근거 다시 조회', panel: 'refresh' }
    : reviewNeeded ? { text: '원문·판정 재확인', panel: 'criteria' }
    : !currentDetail || !currentOptions ? { text: '검토 조건 확인 중', panel: 'loading' }
    : currentDetail.judgement.status === 'EVIDENCE_FOUND' && !currentOptions.options.length && !recordedDevelopment && !selectionRecord && !hasSavedPath ? { text: '충족 근거 확인', panel: 'criteria' }
    : customerValueMissing ? { text: '고객 확인 질문 작성', panel: 'questions' }
    : commercial ? { text: '검토 질문과 기록 확인', panel: 'questions' }
    : designated ? { text: '조달 확인 질문과 기록 확인', panel: 'questions' }
    : task ? { text: '담당·수행 범위와 제출 근거 확인', panel: showDevelopment ? 'development' : 'selection' }
    : currentDetail.judgement.rnd_check === 'PENDING' ? { text: '개발 회신 확인', panel: 'development' }
    : selectionRecord || hasSavedPath ? { text: '기록된 경로·남은 조건 확인', panel: 'selection' }
    : currentDetail.judgement.status === 'EVIDENCE_FOUND' ? { text: '충족 근거 확인', panel: 'criteria' }
    : showDevelopment ? { text: '기술·개발 검토 확인', panel: 'development' }
    : { text: '확인 질문과 기록 보기', panel: 'questions' };
  const workTitle = commercial ? '보증·상업 조건 검토' : designated ? '지정품 조달 검토' : task ? '수행 범위·담당 검토' : '대응 방안 검토';
  const heading = commercial ? '보증·상업 조건 확인이 필요합니다' : designated ? '지정품 조달 조건을 확인하세요' : task ? '수행 범위와 담당을 확인하세요' : currentDetail?.judgement.status === 'EVIDENCE_FOUND' ? '저장된 충족 근거를 확인하세요' : '고객 요구와 자사 조건을 확인하세요';
  const actionExplanation = commercial ? '영업·보증 조건 변경 가능 여부와 남은 조건을 확인하세요.'
    : designated ? '지정 모델의 조달 조건과 고객 허용 범위를 확인하세요.'
    : task ? '수행 담당, 업무 범위와 제출할 자료를 확인하세요.'
    : showDevelopment ? '개발 확인 상태와 요청·회신 근거를 확인하세요.'
    : '미확인 조건을 검토하고 근거에 맞는 대응 경로를 선택하세요.';
  const ownSummary = comparison.isError ? '조회 실패' : comparison.isSuccess && !currentComparison ? '판본 확인 필요' : !currentComparison ? '확인 중'
    : currentComparison.search_trace.search_failed ? '검색 실패 · 재확인 필요'
    : `저장 후보 ${currentComparison.candidates.length}개`;
  const partnerSummary = candidates.isError ? '조회 실패' : !candidates.isSuccess ? '확인 중'
    : !candidateVersionsMatch ? '판본 확인 필요'
    : `제외되지 않은 후보 ${candidateRows.filter(c => currentGrade(c).some(g => g.grade !== 'EXCLUDED')).length}곳`;
  const refresh = () => { void detail.refetch(); void comparison.refetch(); void options.refetch(); void candidates.refetch(); };
  const openNext = () => {
    if (next.panel === 'refresh') refresh();
    else openWorkspace(next.panel);
  };
  return <div className="rfp-response-decision rfp-supplement">
    <section className="response-current" aria-label="선택 요구 현재 상태">
      <div className="response-current-identify"><span>{requirement.equipment ?? requirement.task ?? '선택 요구'}</span><small>{requirementNo(requirement.code)}</small></div>
      <h3>{issue ? '최신 근거 확인이 필요합니다' : heading}</h3><p>{attributeName(requirement.attribute_key, requirement.attribute)}</p>
      <div className="response-current-status"><strong>{currentDetail ? commercial && currentDetail.judgement.status === 'SPEC_GAP' ? '상업·보증 조건 차이' : label(STATUS_LABEL, currentDetail.judgement.status) : detail.isError || detail.isSuccess ? '판정 확인 필요' : '판정 확인 중'}</strong>{reviewNeeded && <span role="status">원문 확인·재검토 필요</span>}</div>
      <div className="response-comparison" aria-label="고객 요구와 자사 조건 비교">
        <div className="response-customer"><b>고객 요구</b><p>{currentDetail ? displayValue(currentDetail.spec.value, currentDetail.spec.unit) || requirement.value_text || '값 확인 필요' : requirement.value_text || '값 확인 중'}</p>{currentDetail && <small>{({ EQ: '동일 조건', GTE: '이상', LTE: '이하', RANGE: '범위 조건', TEXT: '문자 조건' })[currentDetail.spec.operator]}</small>}{currentDetail?.spec.conditions.map((condition, index) => <p key={index}><small>적용 조건: {condition.name} · {displayValue(condition.value, condition.unit)}</small></p>)}</div>
        <div><b>자사에 저장된 조건</b>{currentComparison && !currentComparison.search_trace.search_failed ? currentComparison.candidates.slice(0, 3).map(c => {
          const m = c.attribute_match;
          const wanted = currentDetail?.spec.value;
          const sameUnit = !!currentDetail?.spec.unit && displayUnit(currentDetail.spec.unit) === displayUnit(m.our_unit);
          const numeric = (v: string | null | undefined) => v != null && /^[-+]?\d+(\.\d+)?$/.test(v.trim());
          const attributeMatches = !!currentDetail?.spec.attribute_key && currentDetail.spec.attribute_key === m.matched_attribute;
          const gap = m.comparable && m.result === 'DIFFERS' && !m.our_conditions.length && !currentDetail?.spec.conditions.length && attributeMatches && sameUnit && currentDetail?.judgement.status !== 'CONDITIONAL' && ['EQ', 'GTE'].includes(currentDetail?.spec.operator ?? '') && numeric(wanted) && numeric(m.our_value) && Number.isFinite(Number(wanted)) && Number.isFinite(Number(m.our_value)) ? Math.abs(Number(wanted) - Number(m.our_value)) : undefined;
          return <article key={`${c.product_code}:${c.variant}`}><strong>{c.product_code}{c.variant ? ` · ${c.variant}` : ''}</strong><p>{attributeMatches ? `${displayValue(m.our_value, m.our_unit) || '자료 없음'} · ${resultLabel[m.result]}` : '이 요구와 비교할 속성 확인 필요'}</p>{gap !== undefined && gap > 0 && <small>고객 요구와 {new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 6 }).format(gap)} {displayUnit(m.our_unit)} 차이</small>}{m.result === 'MEETS' && currentDetail?.judgement.status === 'CONDITIONAL' && <small>적용 조건 확인 필요</small>}{!m.comparable && <small>단순 수치 비교 불가 · 적용 조건 확인 필요</small>}{m.our_conditions.map((condition, index) => <p key={index}><small>자사 적용 조건: {condition.name} · {displayValue(condition.value, condition.unit)}</small></p>)}</article>;
        }) : <p>{currentComparison?.search_trace.search_failed ? '검색 실패 · 조건 확인 필요' : ownSummary}</p>}{currentComparison && !currentComparison.candidates.length && <p>현재 검색 범위에 저장된 자사 조건 없음</p>}</div>
      </div>
      <p className="response-comparison-note">{currentComparison && currentComparison.candidates.length > 3 ? `비교 후보 ${currentComparison.candidates.length}개 중 3개 표시 · 전체는 비교 근거와 원문에서 확인하세요. ` : ''}{currentComparison && currentComparison.candidates.length > 1 ? '비교 후보별 조건입니다. 대응 제품은 아직 선택되지 않았습니다.' : '표시된 비교 제품의 해당 조건입니다. 설비 전체의 충족 여부를 뜻하지 않습니다.'}</p>
      {(selectionRecord || hasSavedPath) && <p className="response-saved-path">{selectionRecord ? `보완 선택 기록 · ${label(PATH_LABEL, selectionRecord)}` : `판정에 기록된 경로 · ${label(PATH_LABEL, savedPath)}`}{!selectionRecord && currentDetail?.judgement.provenance_kind === 'ai_draft' ? ' · AI 제안' : ''}{reviewNeeded || selectionRecord && selectionRecord !== savedPath || currentOptions && !currentOptions.options.some(o => o.path === (selectionRecord || savedPath) && o.allowed) ? ' · 재확인 필요' : ''}</p>}
      {!guided && <p className="response-action-explanation">{actionExplanation}</p>}{(!guided || issue || reviewNeeded) && activePanel !== next.panel && <div className="response-next-action"><span>다음 할 일</span><button className="kb primary" type="button" onClick={openNext} disabled={next.panel === 'loading'}>{next.text} →</button><Link to={`/tft/projects/${projectId}/requirements/${id}`}>요구 상세</Link></div>}
      {!canSelect && <p className="response-readonly">현재 읽기 전용 · 담당자 권한과 최신 조회 상태를 확인하세요.</p>}
    </section>
    <div className="response-visible-alerts">
      {(commercial || designated) && recordedDevelopment && <p role="alert">현재 대응 조건과 별도로 개발 검토 기록이 남아 있습니다. 요청·회신 근거를 확인하세요.</p>}
      {selectionLinkMismatch && <p role="alert">대응 경로와 후보의 연결이 일치하지 않습니다. 최신 근거를 다시 조회하세요.</p>}
      {statusMismatch && <p role="alert">목록과 상세의 판정 상태가 다릅니다. 상세 상태를 표시합니다. 최신 근거를 다시 조회하세요.</p>}
      {detail.isError && <ErrorState error={detail.error} onRetry={() => detail.refetch()} />}
      {comparison.isError && <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} />}
      {options.isError && <ErrorState error={options.error} onRetry={() => options.refetch()} />}
      {candidates.isError && <ErrorState error={candidates.error} onRetry={() => candidates.refetch()} />}
      {detail.isSuccess && !currentDetail && <p role="alert">요구 상세의 판본이 일치하지 않습니다.</p>}
      {comparison.isSuccess && !currentComparison && <p role="alert">현재 요구 판본의 자사 비교 결과가 아닙니다. 최신 조회가 필요합니다.</p>}
      {options.isSuccess && !currentOptions && <p role="alert">현재 요구 판본의 대응 경로를 확인할 수 없습니다.</p>}
      {candidates.isSuccess && !candidateVersionsMatch && <p role="alert">현재 요구·프로필 판본과 연결된 등급을 확인할 수 없습니다.</p>}
    </div>
    <div className="response-workspace-actions" role="group" aria-label="검토 작업 선택">
      {guided && activePanel !== 'questions' && <button className="kb" type="button" aria-expanded={activePanel === 'questions'} aria-controls="response-questions-workspace" onClick={() => openWorkspace('questions')}>질문·회신 기록</button>}
      <button className="kb" type="button" aria-expanded={activePanel === 'selection'} aria-controls="response-selection-workspace" onClick={() => openWorkspace('selection')}>대응 방안 기록</button>
      {showDevelopment && !(activePanel === undefined && next.panel === 'development') && <button className="kb" type="button" aria-expanded={activePanel === 'development'} aria-controls="response-development-workspace" onClick={() => openWorkspace('development')}>개발팀 가능성·일정 확인</button>}
      {showPartners && <button className="kb" type="button" aria-expanded={activePanel === 'partners'} aria-controls="response-partners-workspace" onClick={() => openWorkspace('partners')}>협력사 후보 비교</button>}
      <button className="kb" type="button" aria-expanded={activePanel === 'criteria'} aria-controls="response-criteria-workspace" onClick={() => activePanel === 'criteria' ? setExpanded(undefined) : openWorkspace('criteria')}>비교 근거와 원문</button>
      {activePanel && <button className="kb" type="button" onClick={() => setExpanded('closed')}>검토 작업 닫기</button>}
    </div>
    <div ref={workspace} className="response-workspace" hidden={!activePanel} aria-label="선택한 검토 작업">
    {guided && currentDetail && <div id="response-questions-workspace" hidden={activePanel !== 'questions'}><GuidedRequirementReview key={`${id}:${version}`} projectId={projectId} actorId={actorId} requirement={currentDetail} departments={reviewDepartments} canWrite={canSelect && !detail.isFetching} customerValueMissing={customerValueMissing} commercial={commercial} designated={!!designated} onSaved={onSaved} /></div>}
    {showDevelopment && <div id="response-development-workspace" hidden={activePanel !== 'development'}>{visited.has('development') && <DevelopmentReviewFlow taskReview={task} embedded key={`${id}:${version}`} projectId={projectId} actorId={actorId} requirement={currentDetail} requirementId={id} requirementVersion={version} canRequest={canSelect && !detail.isFetching} reviewDepartments={reviewDepartments} projectVersion={projectVersion} canEditProject={canEditProject} onProjectSaved={onProjectSaved} partnerCount={candidates.isSuccess && candidateVersionsMatch ? candidateRows.filter(c => currentGrade(c).some(g => g.grade !== 'EXCLUDED')).length : undefined} onSaved={onSaved} />}</div>}
{showPartners && <section id="response-partners-workspace" hidden={activePanel !== 'partners'} className="response-card response-disclosure" aria-label="협력사 후보">
      <header><h4>협력사 후보</h4><small>{partnerSummary}</small></header>{visited.has('partners') && <><div className="response-disclosure-body">
      {candidates.isPending && <Loading label="협력사 후보 조회 중" />}
      {candidates.isSuccess && <><p>가상 협력사 · 이 요구와 프로필 판본의 저장 등급만 표시합니다.</p><div className="response-partner-list">{candidateRows.map(c => <article className="response-evidence" key={c.candidate_code}><b>{c.candidate_code} · {c.display_name}</b><p>프로필 v{c.profile_version} · 인증 {c.certifications.join(' · ') || '미기재'}</p>{currentGrade(c).length === 1 ? currentGrade(c).map(g => <div key={`${g.requirement_id}:${g.profile_version}`}><strong>{gradeLabel[g.grade]}</strong><p>{g.reasons.map(reasonLabel).join(' · ') || '이유 미기재'}</p><p>제외 사유: {g.exclusion_reasons.map(reasonLabel).join(' · ') || '없음'}</p><p>미충족: {g.mandatory_check.failed.map(reasonLabel).join(' · ') || '없음'} · 미확인: {g.mandatory_check.unknown.map(reasonLabel).join(' · ') || '없음'}</p><small>요구 v{g.requirement_version} · 프로필 v{g.profile_version} · 등급 v{g.version ?? '미기재'}</small></div>) : <p>현재 판본의 등급 상세를 표시할 수 없습니다.</p>}</article>)}</div>{!candidateRows.length && <p>저장된 후보가 없습니다.</p>}</>}
    </div></>}
    </section>}
    <section id="response-criteria-workspace" hidden={activePanel !== 'criteria'} className="response-card response-disclosure response-criteria" aria-label="비교 근거와 원문">
      <header><h4>비교 근거와 원문</h4><small>원문 인용·미확인·판본</small></header>{visited.has('criteria') && <><div className="response-disclosure-body">
      <h4>제품별 비교 출처</h4>
      {comparison.isPending ? <Loading label="자사 비교 조회 중" /> : comparison.isError ? null : !currentComparison ? null : <>
        <div className="response-own-list">{currentComparison.candidates.map(c => <article className="response-evidence" key={`${c.product_code}:${c.variant}`}><b>{c.product_code} {c.variant}</b><p>{[c.attribute_match.our_value, c.attribute_match.our_unit].filter(Boolean).join(' ') || '자료 없음'} · {resultLabel[c.attribute_match.result]}</p><details><summary>비교 근거</summary>{c.attribute_match.evidence.map((e, index) => <p key={index}>근거: {[e.document, e.version, e.clause, e.applicability, e.limits].filter(Boolean).join(' · ')}</p>)}{!c.attribute_match.evidence.length && <p>근거 미기재</p>}</details></article>)}</div>
        {!currentComparison.candidates.length && <p>{currentComparison.search_trace.search_failed ? '자사 후보 검색 실패' : '현재 검색 범위의 자사 후보 없음'}</p>}
        <small>요구 v{currentComparison.requirement_version} · 비교 v{currentComparison.version} · {currentComparison.known_at}</small>
      </>}

      {detail.isPending && <Loading label="요구 상세 조회 중" />}
      {currentDetail ? <><details><summary>요구 인용문 확인</summary><blockquote>{currentDetail.origin.quote}</blockquote></details><p>{currentDetail.judgement.reason}</p><p>{[...currentDetail.spec.undetermined_items, ...currentDetail.judgement.unresolved].join(' · ') || '기록된 미해결 항목 없음'}</p>{(currentDetail.needs_review || currentDetail.needs_rereview) && <p>원문 확인·재검토가 필요합니다.</p>}</> : null}
      {currentOptions?.options.map(o => <div className="response-gate" key={o.path}><b>{label(PATH_LABEL, o.path)} · {o.allowed ? '허용' : '차단'}</b><p>{o.conditions_to_confirm.join(' · ') || '추가 조건 미기재'} · {label(RND_CHECK_LABEL, o.gate.status)}</p>{!o.allowed && <p>{label(BLOCKED_LABEL, o.blocked_reason) || '차단 사유 확인 필요'}</p>}</div>)}
      <p>개별 요구의 근거 있음은 설비 전체 충족을 뜻하지 않습니다. 후보·자료가 없다는 사실만으로 외주나 대응 불가를 확정하지 않습니다.</p>
      <small>선택 요구 v{version} · 비교 v{currentComparison?.version ?? '미확인'}</small>
      <Link to={`/tft/projects/${projectId}/requirements/${id}`}>상세 검토·변경 제안·질문</Link>
    </div></>}
    </section>
    <section id="response-selection-workspace" hidden={activePanel !== 'selection'} className="response-card response-disclosure response-selection" aria-label="대응 경로 선택">
      <header><h4>{workTitle}</h4><small>{!currentDetail ? '확인 중' : selectionRecord ? `선택 기록 · ${label(PATH_LABEL, selectionRecord)}` : hasSavedPath ? '판정 경로 있음 · 사람 선택 미확인' : '검토 방안 미선택'}</small></header>{visited.has('selection') && <><div className="response-disclosure-body">

      {commercial && !termsOption && <p role="alert">현재 서버가 조건 변경 검토 경로를 제공하지 않았습니다. 원문과 최신 경로를 확인하세요.</p>}
      <details className="response-route-conditions"><summary>대응 방안과 제한 조건</summary>{currentOptions?.options.map(o => <div className="response-action-conditions" key={o.path}><b>{label(PATH_LABEL, o.path)} · {o.allowed ? '기록 허용' : '조건 확인 필요'}</b><p>{o.conditions_to_confirm.join(' · ') || '구체적인 확인 조건이 기록되지 않았습니다.'}</p>{o.required_expertise.length > 0 && <p>검토할 전문 분야: {o.required_expertise.map(reasonLabel).join(' · ')}</p>}{!o.allowed && <p>{label(BLOCKED_LABEL, o.blocked_reason) || '차단 사유 확인 필요'}</p>}</div>)}</details>
      <button className="kb" type="button" onClick={refresh} disabled={detail.isFetching || comparison.isFetching || options.isFetching || candidates.isFetching}>대응 근거 최신 조회</button>
      {!canSelect && <p>조회 권한으로 열었습니다. 대응 경로 저장은 담당자만 가능합니다.</p>}
      {options.isPending && <Loading label="대응 경로 조회 중" />}
      {selectionReady && currentOptions ? <SelectionEditor key={`${id}:${version}:${options.dataUpdatedAt}:${candidates.dataUpdatedAt}`} compact context={commercial ? 'commercial' : designated ? 'designated' : task ? 'task' : 'technical'} actorId={actorId} attempts={attempts.current} onSaveReady={noop} options={currentOptions} candidates={candidateRows} canSelect={canSelect && !detail.isFetching && !options.isFetching && !candidates.isFetching} onSaved={onSaved} /> : !options.isPending && !candidates.isPending && <p role="alert">요구·경로·후보의 판본과 연결을 확인한 후 선택할 수 있습니다.</p>}
    </div></>}
    </section>
    </div>
  </div>;
}
