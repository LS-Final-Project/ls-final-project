import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { me } from '../api/platform';
import { getProject } from '../api/tft';
import {
  getSupplementOptions, listSupplementCandidates, listSupplementRequirements, selectSupplement,
  type PartnerCandidate, type RequirementSummary, type SupplementOption, type SupplementOptions,
  type SupplementSelected, type SupplementSelection,
} from '../api/rfp-supplement';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime, Origin } from '../components/tft';
import { KvBox, KvField, KvGrid, KvNext, KvTitle, type KvColumn } from '../ls-shell/kv';

const PATH: Record<SupplementOption['path'], string> = {
  OTHER_OWN_PRODUCT: '자사 다른 제품', OWN_DEVELOPMENT: '자사 개발·수행 확인', OWN_TERMS_REVIEW: '자사 상업 조건 검토',
  ALTERNATIVE_SPEC: '대체 사양', EXTERNAL_PRODUCT: '외부 제품', TASK_SUBCONTRACT: '업무 외주',
  JOINT_PROPOSAL: '공동 제안', CUSTOMER_SPECIFIED_PROCUREMENT: '고객 지정품 조달', NONE: '보완 경로 없음',
};
const RND: Record<string, string> = { NOT_REQUIRED: '확인 불필요', PENDING: 'R&D 확인 대기', CONFIRMED_FEASIBLE: '자사 개발 가능', CONFIRMED_NOT_FEASIBLE: '자사 개발 불가', CONFIRMED_SCHEDULE_MISMATCH: '일정 불일치', WAIVED: '확인 면제' };
const text = (items: readonly string[]) => items.length ? items.join(' · ') : '없음';

export default function RfpSupplement() {
  const { projectId = '' } = useParams();
  const access = useBusinessAccess('tft');
  const user = useQuery({ queryKey: ['me'], queryFn: me, enabled: access.ready, retry: false });
  if (!access.ready) return <BusinessGate access={access} title="보완 및 협력사 비교" />;
  if (user.isPending) return <Loading label="선택 권한 확인 중" />;
  if (user.isError) return <ErrorState error={user.error} onRetry={() => user.refetch()} />;
  if (!user.data) return <ErrorState error={new ApiError(401, '로그인이 필요합니다.')} />;
  return <SupplementProject key={`${getApiMode()}:${user.data.data.id}:${projectId}`} projectId={projectId} userId={user.data.data.id} />;
}

function SupplementProject({ projectId, userId }: { projectId: string; userId: string }) {
  const mode = getApiMode();
  const prefix = ['rfp-supplement', mode, userId, projectId];
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const [requirementId, setRequirementId] = useState('');
  const cursor = cursors.at(-1);
  const project = useQuery({ queryKey: [...prefix, 'project'], queryFn: () => getProject(projectId) });
  const requirements = useQuery({ queryKey: [...prefix, 'requirements', cursor], queryFn: () => listSupplementRequirements(projectId, cursor), enabled: project.isSuccess });
  const rows = requirements.data?.data.items ?? [];
  const selected = rows.find(row => row.requirement_id === requirementId);
  const canSelect = project.isSuccess && [project.data.data.sales_user_id, project.data.data.responsible_user_id].includes(userId);
  const columns: KvColumn<RequirementSummary>[] = [
    { key: 'code', header: '요구', cell: r => <button className="kb" aria-pressed={r.requirement_id === requirementId} onClick={() => setRequirementId(r.requirement_id)}>{r.code}</button> },
    { key: 'spec', header: '대상·내용', cell: r => [r.equipment, r.task, r.attribute, r.value_text].filter(Boolean).join(' · ') || '내용 미기재' },
    { key: 'status', header: '판단', cell: r => r.status },
    { key: 'path', header: '현재 보완 경로', cell: r => r.path ? PATH[r.path] : '미선택' },
    { key: 'version', header: '요구 판본', cell: r => r.version },
    { key: 'review', header: '재검토', cell: r => r.needs_rereview ? '필요' : '없음' },
  ];
  const page = (next: (string | undefined)[]) => { setRequirementId(''); setCursors(next); };
  return <main>
    <KvTitle path="제안 지원" title="보완 및 협력사 비교" code="TF0103"><Link to={`/tft/projects/${projectId}`}>프로젝트 상세</Link></KvTitle>
    <KvNext>요구를 고르고 보완 경로와 후보 근거를 확인한 뒤 역할 분담을 저장하세요.</KvNext>
    {project.isPending && <Loading />}
    {project.isError && <ErrorState error={project.error} onRetry={() => project.refetch()} />}
    {project.isSuccess && <>
      <KvBox title={project.data.data.name} note={canSelect ? '담당 sales·책임자 선택 가능' : '읽기 전용 — 담당 sales·책임자만 선택 가능'}>
        <p>요구 목록 {cursors.length}쪽 · {requirements.isSuccess ? `${rows.length}건` : requirements.isError ? '조회 실패' : '조회 중'}</p>
        <button className="kb" disabled={requirements.isFetching} onClick={() => { setRequirementId(''); void requirements.refetch(); }}>요구 목록 조회</button>{' '}
        <button className="kb" disabled={requirements.isFetching || cursors.length === 1} onClick={() => page(cursors.slice(0, -1))}>이전</button>{' '}
        <button className="kb" disabled={requirements.isFetching || !requirements.data?.data.next_cursor} onClick={() => page([...cursors, requirements.data!.data.next_cursor!])}>다음</button>
        {requirements.isError ? <ErrorState error={requirements.error} onRetry={() => requirements.refetch()} /> : <KvGrid label="보완 대상 요구" columns={columns} rows={rows} rowKey={r => r.requirement_id} fallback={requirements.isPending ? <Loading /> : '정리된 요구가 없습니다.'} />}
      </KvBox>
      {selected && requirements.isSuccess && <RequirementSupplement key={selected.requirement_id} projectId={projectId} requirement={selected} canSelect={canSelect} prefix={prefix} />}
    </>}
  </main>;
}

function RequirementSupplement({ projectId, requirement, canSelect, prefix }: {
  projectId: string; requirement: RequirementSummary; canSelect: boolean; prefix: string[];
}) {
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState<SupplementSelected | null>(null);
  const attempts = useRef(new Map<string, string>());
  const options = useQuery({ queryKey: [...prefix, requirement.requirement_id, 'options'], queryFn: () => getSupplementOptions(requirement.requirement_id), refetchOnWindowFocus: false });
  const candidates = useQuery({ queryKey: [...prefix, requirement.requirement_id, 'candidates'], queryFn: () => listSupplementCandidates(projectId, requirement.requirement_id), refetchOnWindowFocus: false });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: [...prefix, 'requirements'] });
    void options.refetch(); void candidates.refetch();
  };
  return <KvBox title={`${requirement.code} 보완 선택`}>
    <button className="kb" disabled={options.isFetching || candidates.isFetching} onClick={refresh}>보완 최신 조회</button>
    <p>최신 조회 시 미저장 선택을 다시 입력합니다. R&D 답변이나 요구 변경을 확인한 뒤 저장하세요.</p>
    {options.isPending || candidates.isPending ? <Loading /> : null}
    {options.isError && <ErrorState error={options.error} onRetry={refresh} />}
    {candidates.isError && <ErrorState error={candidates.error} onRetry={refresh} />}
    {saved && <div role="status"><p>보완 선택 저장 완료 · 선택 판본 {saved.version} · {PATH[saved.path]}</p><p>역할 분담: {saved.role_split}</p><p>후보: {text(saved.candidate_codes)} · 조건: {text(saved.conditions)}</p><p>결정자 {saved.decided_by} · {formatDateTime(saved.decided_at)}</p></div>}
    {options.isSuccess && candidates.isSuccess && <>
      <Origin dataOrigin={options.data.data.data_origin} derivationKind={options.data.data.derivation_kind} />
      <KvField label="부족 원인">{options.data.data.deficiency_cause ?? '미확인'}</KvField>
      <KvField label="고객 허용">{options.data.data.customer_permission} {options.data.data.permission_clause_id && `· 근거 조항 ${options.data.data.permission_clause_id}`}</KvField>
      <KvField label="요구 판본">{options.data.data.version}</KvField>
      <KvField label="자료 기준 시각">{formatDateTime(options.data.data.known_at)}</KvField>
      {options.data.data.version !== requirement.version
        ? <p role="alert">요구 판본이 변경되었습니다. 보완 최신 조회 후 내용을 확인하세요.</p>
        : <SelectionEditor key={`${options.dataUpdatedAt}:${candidates.dataUpdatedAt}`} attempts={attempts.current} options={options.data.data} candidates={candidates.data.data.items} canSelect={canSelect && !options.isFetching && !candidates.isFetching} onSaved={value => { setSaved(value); void queryClient.invalidateQueries({ queryKey: [...prefix, 'requirements'] }); }} />}
    </>}
  </KvBox>;
}

function SelectionEditor({ options, candidates, canSelect, onSaved, attempts }: {
  attempts: Map<string, string>; options: SupplementOptions; candidates: PartnerCandidate[]; canSelect: boolean; onSaved: (value: SupplementSelected) => void;
}) {
  const [path, setPath] = useState<SupplementOption['path'] | ''>('');
  const [codes, setCodes] = useState<string[]>([]);
  const [roleSplit, setRoleSplit] = useState('');
  const [conditions, setConditions] = useState('');
  const [note, setNote] = useState('');
  const [waiver, setWaiver] = useState('');
  const [conflict, setConflict] = useState(false);
  const option = options.options.find(o => o.path === path);
  const needsWaiver = option?.gate.requires === 'OWN_DEVELOPMENT_CHECK' && option.gate.status === 'PENDING';
  const excluded = (code: string) => candidates.find(c => c.candidate_code === code)?.grades.some(g => g.requirement_id === options.requirement_id && g.grade === 'EXCLUDED') === true;
  const save = useMutation({ mutationFn: async (payload: SupplementSelection) => {
    const body = JSON.stringify(payload);
    if (!attempts.has(body)) attempts.set(body, crypto.randomUUID());
    return selectSupplement(options.requirement_id, payload, attempts.get(body)!);
  }, retry: false, onSuccess: value => onSaved(value.data), onError: error => {
    if (error instanceof ApiError && error.status === 409) setConflict(true);
  } });
  const valid = canSelect && !conflict && !!option?.allowed && !!roleSplit.trim() && codes.every(c => option.candidate_codes.includes(c) && !excluded(c)) && (!needsWaiver || waiver.trim().length >= 20);
  const columns: KvColumn<SupplementOption>[] = [
    { key: 'choose', header: '선택', cell: o => <input type="radio" name="supplement-path" aria-label={PATH[o.path]} checked={path === o.path} disabled={!canSelect || !o.allowed || save.isPending || conflict} onChange={() => { setPath(o.path); setCodes([]); setWaiver(''); }} /> },
    { key: 'rank', header: '순서', cell: o => o.rank },
    { key: 'path', header: '보완 경로', cell: o => PATH[o.path] },
    { key: 'allowed', header: '허용·차단 사유', cell: o => o.allowed ? '선택 가능' : `선택 불가 · ${o.blocked_reason ?? '차단'}` },
    { key: 'gate', header: 'R&D 확인', cell: o => RND[o.gate.status] ?? o.gate.status },
    { key: 'conditions', header: '확인 조건·전문성', cell: o => <>{text(o.conditions_to_confirm)}<br />전문성: {text(o.required_expertise)}</> },
  ];
  return <>
    <KvGrid label="보완 경로 선택지" columns={columns} rows={[...options.options].sort((a, b) => a.rank - b.rank)} rowKey={o => o.path} fallback="현재 선택 가능한 보완 경로가 없습니다." />
    <KvBox title="협력사 후보 근거" note="가상 협력사 · 등급은 선택한 요구 기준이며 전체 등급과 구분합니다.">
      <CandidateEvidence candidates={candidates} requirementId={options.requirement_id} />
    </KvBox>
    <KvBox title="보완 선택">
        <p>사람의 결정으로 저장하며 새 선택 판본을 만듭니다.</p>
        <fieldset disabled={!canSelect || conflict || save.isPending}>
          <legend>{option ? PATH[option.path] : '보완 경로를 먼저 선택하세요'}</legend>
          {option?.candidate_codes.map(code => <label key={code} style={{ display: 'block' }}><input type="checkbox" aria-label={`후보 ${code}`} checked={codes.includes(code)} disabled={excluded(code) || (!codes.includes(code) && codes.length >= 10)} onChange={e => setCodes(e.target.checked ? [...codes, code] : codes.filter(c => c !== code))} />{code} {candidates.find(c => c.candidate_code === code)?.display_name} {excluded(code) && '(제외 후보)'}</label>)}
          {option && !option.candidate_codes.length && <p>이 경로에 등록된 후보가 없습니다.</p>}
          <label>역할 분담<textarea aria-label="역할 분담" value={roleSplit} maxLength={2000} onChange={e => setRoleSplit(e.target.value)} /></label>
          <label>남는 조건 (한 줄에 하나)<textarea aria-label="남는 조건" value={conditions} onChange={e => setConditions(e.target.value)} /></label>
          <label>선택 메모<textarea aria-label="선택 메모" value={note} maxLength={1000} onChange={e => setNote(e.target.value)} /></label>
          {needsWaiver && <label>R&D 확인 면제 사유 (20자 이상)<textarea aria-label="R&D 확인 면제 사유" value={waiver} maxLength={1000} onChange={e => setWaiver(e.target.value)} /></label>}
        </fieldset>
        {needsWaiver && <p>자사 개발 확인 전 협력사 경로를 선택하면 확인 면제로 기록됩니다.</p>}
        {conflict && <p role="alert">판본 또는 선택 요청이 충돌했습니다. 보완 최신 조회 후 다시 확인하세요.</p>}
        {save.isError && <ErrorState error={save.error} />}
        <button className="kb primary" disabled={!valid || save.isPending} onClick={() => option && save.mutate({ version: options.version, path: option.path, candidate_codes: [...codes].sort(), role_split: roleSplit.trim(), conditions: conditions.split('\n').map(c => c.trim()).filter(Boolean), note: note.trim() || null, rnd_check_waiver_reason: needsWaiver ? waiver.trim() : null })}>{save.isPending ? '저장 중' : '보완 선택 저장'}</button>
      </KvBox>
  </>;
}

const GRADE: Record<string, string> = { MANDATORY_CONFIRMED: '필수 기술조건 확인', REVIEW: '검토', REFERENCE_ONLY: '확인용', EXCLUDED: '제외' };
function CandidateEvidence({ candidates, requirementId }: { candidates: PartnerCandidate[]; requirementId: string }) {
  const grades = (c: PartnerCandidate) => c.grades.filter(g => g.requirement_id === requirementId);
  const columns: KvColumn<PartnerCandidate>[] = [
    { key: 'name', header: '후보·출처', cell: c => <>{c.candidate_code} {c.display_name}<p>{c.profile_source} · 프로필 판본 {c.profile_version} · {c.confirmation_status}</p></> },
    { key: 'scope', header: '수행 범위·사양', cell: c => <>{text(c.scope)}<p>{text(c.spec_facts_attributes)}</p></> },
    { key: 'certification', header: '인증', cell: c => text(c.certifications) },
    { key: 'customer', header: '고객 목록', cell: c => c.on_customer_manufacturer_list === null ? '미확인' : c.on_customer_manufacturer_list ? '포함' : '미포함' },
    { key: 'delivery', header: '납기·보증', cell: c => <>{c.lead_time ? `${c.lead_time.value}주 · ${c.lead_time.ex_works ? '공장 출고 기준' : '공장 출고 기준 아님'}` : '미확인'}<p>{c.warranty ?? '미확인'}</p></> },
    { key: 'grade', header: '이 요구의 등급', cell: c => grades(c).length ? grades(c).map(g => GRADE[g.grade] ?? g.grade).join(' · ') : '등급 없음' },
    { key: 'reason', header: '이유·제외 사유', cell: c => grades(c).map(g => <div key={g.requirement_id}>{text(g.reasons)}<p>제외 사유: {text(g.exclusion_reasons)}</p></div>) },
    { key: 'mandatory', header: '필수 확인', cell: c => grades(c).map(g => <div key={g.requirement_id}>충족: {text(g.mandatory_check.passed)}<p>미충족: {text(g.mandatory_check.failed)}</p><p>미확인: {text(g.mandatory_check.unknown)}</p></div>) },
    { key: 'overall', header: '전체 등급·추가 확인', cell: c => <>{GRADE[c.overall_grade] ?? c.overall_grade}<p>{text(c.conditions_to_confirm)}</p></> },
  ];
  return <KvGrid label="협력사 후보 근거" columns={columns} rows={candidates} rowKey={c => c.candidate_code} fallback="이 요구의 저장된 후보 등급이 없습니다." />;
}
