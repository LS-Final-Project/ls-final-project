import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError, getApiMode } from '../api/client';
import { me } from '../api/platform';
import { getProject } from '../api/tft';
import {
  getSupplementRequirement, getSupplementOptions, listSupplementCandidates, listSupplementRequirements, selectSupplement,
  type PartnerCandidate, type RequirementSummary, type SupplementOption, type SupplementOptions,
  type SupplementSelected, type SupplementSelection,
} from '../api/rfp-supplement';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime, Origin } from '../components/tft';
import type { RequirementFilters } from '../api/rfp';
import './RfpSupplement.css';
import IntegrationPanel from './IntegrationPanel';
import { ATTRIBUTE_LABEL, requirementNo, BLOCKED_LABEL, PATH_LABEL as PATH, RND_CHECK_LABEL, STATUS_LABEL, NECESSITY_LABEL, label } from './labels';
import { KvBox, KvField, KvGrid, KvNext, KvTitle, KvSplit, KvCond, KvCondRow, KvCondField, type KvColumn } from '../ls-shell/kv';

const CAUSE: Record<string, string> = { RATING: '정격', CERTIFICATION: '인증', FORM: '구조·형식', TASK: '수행 작업', DESIGNATED_MODEL: '지정 모델', COMMERCIAL: '거래 조건', CORE_EQUIPMENT_MISSING: '핵심 장비 없음' };
const PERMISSION = { ALLOWED: '허용', FORBIDDEN: '금지', UNSTATED: '미기재' };
const attributeName = (r: RequirementSummary) => r.attribute_key && r.attribute_key !== 'OTHER' ? label(ATTRIBUTE_LABEL, r.attribute_key) : r.attribute;
type SaveReady = (requirementId: string, ready: boolean) => void;
const text = (items: readonly string[]) => items.length ? items.join(' · ') : '없음';

export default function RfpSupplement() {
  const { projectId = '' } = useParams();
  const access = useBusinessAccess('tft');
  const user = useQuery({ queryKey: ['me'], queryFn: me, enabled: access.ready, retry: false });
  if (user.isFetching && !user.data) return <Loading label="선택 권한 확인 중" />;
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
  const [headerSave, setHeaderSave] = useState<{ requirementId: string; ready: boolean } | null>(null);
  const onSaveReady = useCallback<SaveReady>((id, ready) => setHeaderSave(current => current?.requirementId === id && current.ready === ready ? current : { requirementId: id, ready }), []);
  const [status, setStatus] = useState('');
  const [necessity, setNecessity] = useState('');
  const [filters, setFilters] = useState<RequirementFilters>({});
  const attempts = useRef(new Map<string, string>());
  const cursor = cursors.at(-1);
  const project = useQuery({ queryKey: [...prefix, 'project'], queryFn: () => getProject(projectId) });
  const requirements = useQuery({ queryKey: [...prefix, 'requirements', filters, cursor], queryFn: () => listSupplementRequirements(projectId, cursor, filters), enabled: project.isSuccess });
  const rows = requirements.data?.data.items ?? [];
  const selected = rows.find(row => row.requirement_id === requirementId) ?? (!requirementId ? rows[0] : undefined);
  const canSelect = project.isSuccess && [project.data.data.sales_user_id, project.data.data.responsible_user_id].includes(userId);
  const page = (next: (string | undefined)[]) => { setRequirementId(''); setCursors(next); };
  const search = () => { setRequirementId(''); setCursors([undefined]); setFilters({ status: status as RequirementFilters['status'] || undefined, necessity: necessity as RequirementFilters['necessity'] || undefined }); };
  const nextUnselected = rows.find(row => !row.path && row.requirement_id !== selected?.requirement_id);
  return <main className="rfp-supplement">
    <KvTitle path="제안 지원" title="보완 및 협력사 비교" code="TF0103">
      <button className="kb primary" type="submit" form="supplement-selection" disabled={!selected || !canSelect || !requirements.isSuccess || requirements.isFetching || headerSave?.requirementId !== selected.requirement_id || !headerSave.ready}>선택 저장</button>
      <button className="kb" disabled={!nextUnselected} onClick={() => nextUnselected && setRequirementId(nextUnselected.requirement_id)}>다음 미선택</button>
      {selected && <><a className="kb" href="#supplement-candidates">후보 비교</a><Link className="kb" to={`/tft/projects/${projectId}/requirements/${selected.requirement_id}`}>요구 상세</Link></>}
      <Link className="kb" to={`/tft/projects/${projectId}`}>목록</Link>
    </KvTitle>
    <KvNext>{selected ? `${selected.equipment ?? selected.task ?? '선택 요구'} · ${attributeName(selected) ?? '속성 미기재'} · ${label(NECESSITY_LABEL, selected.necessity)} · ${label(STATUS_LABEL, selected.status)} | 다음: 보완 경로·후보 근거 확인 후 선택 저장` : '조회조건을 확인하고 보완할 요구를 선택하세요.'}</KvNext>
    <IntegrationPanel projectId={projectId} canEdit={canSelect} />
    <KvCond onSearch={search}><KvCondRow columns={4}>
      <KvCondField label="프로젝트"><input aria-label="프로젝트" value={project.data?.data.name ?? ''} readOnly /></KvCondField>
      <KvCondField label="판단"><select aria-label="판단" value={status} onChange={e => setStatus(e.target.value)}><option value="">전체</option>{Object.entries(STATUS_LABEL).map(([code, value]) => <option key={code} value={code}>{value}</option>)}</select></KvCondField>
      <KvCondField label="성격"><select aria-label="성격" value={necessity} onChange={e => setNecessity(e.target.value)}><option value="">전체</option>{Object.entries(NECESSITY_LABEL).map(([code, value]) => <option key={code} value={code}>{value}</option>)}</select></KvCondField>
      <div className="supplement-query"><button className="kb primary" type="submit" disabled={requirements.isFetching}>조회</button><button className="kb" type="button" disabled={requirements.isFetching} onClick={() => { setRequirementId(''); void requirements.refetch(); }}>요구 목록 조회</button></div>
    </KvCondRow></KvCond>
    {project.isPending && <Loading />}
    {project.isError && <ErrorState error={project.error} onRetry={() => project.refetch()} />}
    {project.isSuccess && <>
      <div className="supplement-list-heading"><span>조회 요구 {rows.length}건 · {cursors.length}쪽 · {canSelect ? '담당 영업·책임자 선택 가능' : '읽기 전용 — 담당 영업·책임자만 선택 가능'}</span><span><button className="kb" disabled={requirements.isFetching || cursors.length === 1} onClick={() => page(cursors.slice(0, -1))}>이전</button> <button className="kb" disabled={requirements.isFetching || !requirements.data?.data.next_cursor} onClick={() => page([...cursors, requirements.data!.data.next_cursor!])}>다음</button></span></div>
      {requirements.isError ? <ErrorState error={requirements.error} onRetry={() => requirements.refetch()} /> : <nav className="supplement-requirements" aria-label="요구 목록">{rows.map(row => <button key={row.requirement_id} aria-label={requirementNo(row.code)} title={`${row.equipment ?? row.task ?? ''} ${attributeName(row) ?? ''} · ${label(STATUS_LABEL, row.status)} · ${label(NECESSITY_LABEL, row.necessity)}`} aria-pressed={row.requirement_id === selected?.requirement_id} onClick={() => setRequirementId(row.requirement_id)}>{requirementNo(row.code)} {attributeName(row) ?? row.equipment} {row.path ? '· 선택 완료' : ''}</button>)}{requirements.isPending ? <Loading /> : !rows.length && <p>조회조건에 맞는 정리된 요구가 없습니다.</p>}</nav>}
      {selected && requirements.isSuccess && <RequirementSupplement key={selected.requirement_id} projectId={projectId} projectName={project.data.data.name} requirement={selected} canSelect={canSelect && !requirements.isFetching} onSaveReady={onSaveReady} prefix={prefix} actorId={userId} attempts={attempts.current} />}
    </>}
  </main>;
}

function RequirementSupplement({ projectId, projectName, requirement, canSelect, onSaveReady, prefix, attempts, actorId }: {
  projectId: string; projectName: string; requirement: RequirementSummary; canSelect: boolean; onSaveReady: SaveReady; prefix: string[]; attempts: Map<string, string>; actorId: string;
}) {
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState<SupplementSelected | null>(null);
  const detail = useQuery({ queryKey: [...prefix, requirement.requirement_id, 'detail'], queryFn: () => getSupplementRequirement(requirement.requirement_id), refetchOnWindowFocus: false });
  const options = useQuery({ queryKey: [...prefix, requirement.requirement_id, 'options'], queryFn: () => getSupplementOptions(requirement.requirement_id), refetchOnWindowFocus: false });
  const candidates = useQuery({ queryKey: [...prefix, requirement.requirement_id, 'candidates'], queryFn: () => listSupplementCandidates(projectId, requirement.requirement_id), refetchOnWindowFocus: false });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: [...prefix, 'requirements'] });
    void detail.refetch(); void options.refetch(); void candidates.refetch();
  };
  const info = options.data?.data;
  const no = detail.data?.data.origin.clause_no ?? requirementNo(requirement.code);
  return <div className="supplement-detail">
    <div className="supplement-summary">
      <KvField label="프로젝트">{projectName}</KvField>
      <KvField label="요구"><Link to={`/tft/projects/${projectId}/requirements/${requirement.requirement_id}`}>{no} {attributeName(requirement)}</Link></KvField>
      <KvField label="요구값">{requirement.value_text ?? '미기재'}</KvField>
      <KvField label="성격">{label(NECESSITY_LABEL, requirement.necessity)}</KvField>
      <KvField label="상태">{label(STATUS_LABEL, requirement.status)}</KvField>
      <KvField label="부족 원인">{info?.deficiency_cause ? CAUSE[info.deficiency_cause] : '미확인'}</KvField>
      <KvField label="고객 허용">{info ? PERMISSION[info.customer_permission] : '조회 중'}</KvField>
      <KvField label="현재 경로">{requirement.path ? PATH[requirement.path] : '미선택'} · 요구 판본 {requirement.version}</KvField>
    </div>
    <details className="supplement-source"><summary>원문·요구 상세 / 판본과 출처 확인</summary>
      {detail.isPending && <Loading label="원문 조회 중" />}{detail.isError && <ErrorState error={detail.error} onRetry={() => detail.refetch()} />}
      {detail.isSuccess && <><p>{no} · {detail.data.data.origin.page ? `${detail.data.data.origin.page}쪽` : '쪽 미기재'}</p><blockquote>{detail.data.data.origin.quote}</blockquote><p>판단 근거: {detail.data.data.judgement?.reason ?? '미기재'}</p><p>속성 원문: {requirement.attribute ?? '없음'} · 장비·작업: {[requirement.equipment, requirement.task].filter(Boolean).join(' / ') || '없음'}</p></>}
      {info && <><KvField label="요구 판본">{info.version}</KvField><KvField label="자료 기준 시각">{formatDateTime(info.known_at)}</KvField><Origin dataOrigin={info.data_origin} derivationKind={info.derivation_kind} />{info.permission_clause_id && <p>고객 허용 근거 조항 ID: {info.permission_clause_id}</p>}</>}
    </details>
    <div className="supplement-list-heading"><h2 className="supplement-heading">{requirementNo(requirement.code)} 보완 선택</h2><span>보완 경로 {info?.options.length ?? '—'} · 가능 {info?.options.filter(o => o.allowed).length ?? '—'}</span><button className="kb" disabled={options.isFetching || candidates.isFetching} onClick={refresh}>보완 최신 조회</button></div>
    {options.isPending || candidates.isPending ? <Loading /> : null}
    {options.isError && <ErrorState error={options.error} onRetry={refresh} />}
    {candidates.isError && <ErrorState error={candidates.error} onRetry={refresh} />}
    {candidates.isSuccess && <p>고객 목록 포함 여부는 최신 프로젝트 입력{candidates.data.data.input_project_version != null ? ` v${candidates.data.data.input_project_version}` : ""} 기준입니다. 저장 등급은 아래 판정 시점 기준이며 입력 변경이 즉시 재채점됨을 뜻하지 않습니다.</p>}
    {saved && <div role="status"><p>보완 선택 저장 완료 · 선택 판본 {saved.version} · {PATH[saved.path]}</p><p>역할 분담: {saved.role_split}</p><p>후보: {text(saved.candidate_codes)} · 조건: {text(saved.conditions)}</p><p>결정자 {saved.decided_by} · {formatDateTime(saved.decided_at)}</p></div>}
    {options.isSuccess && candidates.isSuccess && <>
      {options.data.data.version !== requirement.version
        ? <p role="alert">요구 판본이 변경되었습니다. 보완 최신 조회 후 내용을 확인하세요.</p>
        : <SelectionEditor key={`${options.dataUpdatedAt}:${candidates.dataUpdatedAt}`} actorId={actorId} attempts={attempts} onSaveReady={onSaveReady} options={options.data.data} candidates={candidates.data.data.items} canSelect={canSelect && !options.isFetching && !candidates.isFetching} onSaved={value => { setSaved(value); void queryClient.invalidateQueries({ queryKey: [...prefix, 'requirements'] }); void queryClient.invalidateQueries({ queryKey: [...prefix, requirement.requirement_id] }); }} />}
    </>}
  </div>;
}

function SelectionEditor({ options, candidates, canSelect, onSaveReady, onSaved, attempts, actorId }: {
  actorId: string; attempts: Map<string, string>; options: SupplementOptions; candidates: PartnerCandidate[]; canSelect: boolean; onSaveReady: SaveReady; onSaved: (value: SupplementSelected) => void;
}) {
  const queryClient = useQueryClient();
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
    const body = JSON.stringify([options.requirement_id, payload]);
    if (!attempts.has(body)) attempts.set(body, crypto.randomUUID());
    return selectSupplement(options.requirement_id, payload, attempts.get(body)!, actorId);
  }, retry: false, onSuccess: value => onSaved(value.data), onError: error => {
    if (error instanceof ApiError && (error.status === 409 || ['TFT_CANDIDATE_EXCLUDED', 'TFT_RND_CHECK_REQUIRED'].includes(error.code ?? ''))) setConflict(true);
    if (error instanceof ApiError && (error.code === 'SESSION_CHANGED' || error.status === 401)) {
      queryClient.setQueryData(['me'], null);
      void queryClient.invalidateQueries({ queryKey: ['me'] });
    }
  } });
  const valid = canSelect && !conflict && !!option?.allowed && !!roleSplit.trim() && codes.every(c => option.candidate_codes.includes(c) && !excluded(c)) && (!needsWaiver || waiver.trim().length >= 20);
  const ready = valid && !save.isPending;
  // Only the mounted editor can enable the header; refetch/unmount clears it before paint.
  useLayoutEffect(() => {
    onSaveReady(options.requirement_id, ready);
    return () => onSaveReady(options.requirement_id, false);
  }, [onSaveReady, options.requirement_id, ready]);
  const choosePath = (value: SupplementOption['path'] | '') => { setPath(value); setCodes([]); setWaiver(''); };
  const columns: KvColumn<SupplementOption>[] = [
    { key: 'choose', header: '선택', width: 38, className: 'c', cell: o => <input type="radio" name="supplement-path" aria-label={PATH[o.path]} checked={path === o.path} disabled={!canSelect || !o.allowed || save.isPending || conflict} onChange={() => choosePath(o.path)} /> },
    { key: 'path', header: '보완 경로', width: 120, cell: o => PATH[o.path] },
    { key: 'allowed', header: '가능', width: 48, className: 'c', cell: o => o.allowed ? '예' : '아니오' },
    { key: 'conditions', header: '확인할 조건', cell: o => <>{text(o.conditions_to_confirm)}{o.gate.requires !== 'NONE' && <small> · {label(RND_CHECK_LABEL, o.gate.status)}</small>}</> },
    { key: 'expertise', header: '필요 전문성', width: 120, cell: o => text(o.required_expertise) },
    { key: 'candidates', header: '후보', width: 48, className: 'c', cell: o => o.candidate_codes.length },
    { key: 'blocked', header: '막힌 이유', width: 240, cell: o => o.allowed ? '—' : label(BLOCKED_LABEL, o.blocked_reason) || '차단' },
  ];
  return <>
    <KvGrid label="보완 경로 선택지" columns={columns} rows={[...options.options].sort((a, b) => a.rank - b.rank)} selectedKey={path} rowKey={o => o.path} fallback="현재 선택 가능한 보완 경로가 없습니다." />
    <details className="supplement-source"><summary>경로 조건·차단 사유 전체</summary>{[...options.options].sort((a, b) => a.rank - b.rank).map(o => <div key={o.path}><strong>{PATH[o.path]}</strong><p>확인 조건: {text(o.conditions_to_confirm)}</p><p>필요 전문성: {text(o.required_expertise)} · R&D 확인: {label(RND_CHECK_LABEL, o.gate.status)}</p>{!o.allowed && <p>막힌 이유: {label(BLOCKED_LABEL, o.blocked_reason) || '차단'}</p>}</div>)}</details>
    <div className="supplement-bottom"><KvSplit columns="minmax(0,1.6fr) minmax(340px,1fr)">
      <div id="supplement-candidates">
        <p className="supplement-candidate-heading">협력사 후보 {candidates.length} · 가상 협력사, 등급은 이 요구 기준</p>
        <CandidateEvidence candidates={candidates} requirementId={options.requirement_id} />
      </div>
      <KvBox title="보완 선택" note="사람의 결정 · 저장하면 새 판본">
        <form id="supplement-selection" onSubmit={event => {
          event.preventDefault();
          if (ready && option) save.mutate({ version: options.version, path: option.path, candidate_codes: [...codes].sort(), role_split: roleSplit.trim(), conditions: conditions.split('\n').map(c => c.trim()).filter(Boolean), note: note.trim() || null, rnd_check_waiver_reason: needsWaiver ? waiver.trim() : null });
        }}>
        <fieldset disabled={!canSelect || conflict || save.isPending}>
          <legend className="supplement-sr">보완 선택 작성</legend>
          <KvField label="보완 경로 *"><select aria-label="보완 경로" required value={path} onChange={e => choosePath(e.target.value as SupplementOption['path'] | '')}><option value="">경로 선택</option>{[...options.options].sort((a, b) => a.rank - b.rank).map(o => <option key={o.path} value={o.path} disabled={!o.allowed}>{PATH[o.path]}</option>)}</select></KvField>
          <KvField label="후보"><div className="supplement-checkboxes">{option?.candidate_codes.map(code => <label key={code}><input type="checkbox" aria-label={`후보 ${code}`} checked={codes.includes(code)} disabled={excluded(code) || (!codes.includes(code) && codes.length >= 10)} onChange={e => setCodes(e.target.checked ? [...codes, code] : codes.filter(c => c !== code))} />{code} {candidates.find(c => c.candidate_code === code)?.display_name} {excluded(code) && '(제외 후보)'}</label>)}{!option && <span>보완 경로를 먼저 선택하세요.</span>}{option && !option.candidate_codes.length && <span>이 경로에 등록된 후보가 없습니다.</span>}</div></KvField>
          <KvField label="역할 분담 *"><textarea aria-label="역할 분담" required rows={3} value={roleSplit} maxLength={2000} onChange={e => setRoleSplit(e.target.value)} /></KvField>
          <KvField label="남는 조건"><textarea aria-label="남는 조건" rows={2} placeholder="한 줄에 하나" value={conditions} onChange={e => setConditions(e.target.value)} /></KvField>
          <KvField label="메모"><input aria-label="선택 메모" value={note} maxLength={1000} onChange={e => setNote(e.target.value)} /></KvField>
          {needsWaiver && <KvField label="R&D 확인 면제"><textarea aria-label="R&D 확인 면제 사유" required minLength={20} rows={2} placeholder="면제 사유 20자 이상" value={waiver} maxLength={1000} onChange={e => setWaiver(e.target.value)} /></KvField>}
        </fieldset>
        {needsWaiver && <p>자사 개발 확인 전 협력사 경로를 선택하면 확인 면제로 기록됩니다.</p>}
        {conflict && <p role="alert">선택 내용을 다시 확인해야 합니다. 보완 최신 조회 후 다시 선택하세요.</p>}
        {save.isError && <ErrorState error={save.error} />}
        <p className="supplement-help">최신 조회 시 미저장 선택을 다시 입력합니다. 허용 안 된 경로와 제외 후보는 고를 수 없습니다.</p>
        <div className="supplement-actions"><button className="kb primary" type="submit" disabled={!ready}>{save.isPending ? '저장 중' : '보완 선택 저장'}</button></div>
        </form>
      </KvBox>
    </KvSplit></div>
  </>;
}

const GRADE: Record<string, string> = { MANDATORY_CONFIRMED: '필수 기술조건 확인', REVIEW: '검토', REFERENCE_ONLY: '확인용', EXCLUDED: '제외' };
const SCOPE: Record<string, string> = { SUPPLY: '공급', ASSEMBLY: '조립', INSTALL: '설치', TEST: '시험', CALC: '계산', TRAINING: '교육' };
const CONFIRMATION: Record<string, string> = { UNCONFIRMED: '미확인', CONTACT_PLANNED: '확인 예정', CONFIRMED: '확인됨' };
const checkLabel = (value: string) => {
  if (value === "extraction_semantics") return "추출 의미 미보존";
  const responsibility: Record<string, string> = { assembly: '조립 책임', supply: '공급 책임', install: '설치 책임', test: '시험 책임', calc: '계산 책임', training: '교육 책임', integration: '통합 책임', warranty: '보증 책임' };
  return value.startsWith('integrator_capable.') ? responsibility[value.slice('integrator_capable.'.length)] ?? '통합 수행 항목(원문 상세)' : ATTRIBUTE_LABEL[value] ?? (/^[a-z][a-z0-9_.]*$/.test(value) ? '사양 항목(원문 상세)' : value);
};
const reasonLabel = (value: string) => value.replace(/\bintegrator_capable\.[a-z_]+|\b[a-z]+(?:_[a-z]+)+\b|\bbil\b/g, key => key.startsWith('integrator_capable.') || ATTRIBUTE_LABEL[key] ? checkLabel(key) : key);
function CandidateEvidence({ candidates, requirementId }: { candidates: PartnerCandidate[]; requirementId: string }) {
  const [code, setCode] = useState('');
  const selected = candidates.find(c => c.candidate_code === code) ?? candidates[0];
  const grades = (c: PartnerCandidate) => c.grades.filter(g => g.requirement_id === requirementId);
  const columns: KvColumn<PartnerCandidate>[] = [
    { key: 'name', header: '후보', width: 55, cell: c => <button className="supplement-link" aria-pressed={c === selected} onClick={() => setCode(c.candidate_code)}>{c.candidate_code}</button> },
    { key: 'scope', header: '수행 범위', width: 98, cell: c => <span title={c.display_name}>{c.display_name}<br />{text(c.scope.map(value => SCOPE[value] ?? value))}</span> },
    { key: 'certification', header: '인증', width: 88, cell: c => text(c.certifications) },
    { key: 'customer', header: '고객 목록(최신 입력)', width: 60, className: 'c', cell: c => c.on_customer_manufacturer_list === null ? '미확인' : c.on_customer_manufacturer_list ? '예' : '아니오' },
    { key: 'delivery', header: '납기', width: 52, className: 'c', cell: c => c.lead_time ? `${c.lead_time.value}주` : '미확인' },
    { key: 'warranty', header: '보증', width: 60, cell: c => c.warranty ?? '미확인' },
    { key: 'grade', header: '등급', width: 110, cell: c => grades(c).length ? grades(c).map(g => GRADE[g.grade] ?? g.grade).join(' · ') : '등급 없음' },
    { key: 'reason', header: '이유', width: 120, cell: c => grades(c).map(g => <span key={g.requirement_id} title={text([...g.reasons, ...g.exclusion_reasons])}>{text([...g.reasons, ...g.exclusion_reasons].map(reasonLabel))}</span>) },
    { key: 'mandatory', header: '필수 확인', width: 66, className: 'c', cell: c => grades(c).map(g => <span key={g.requirement_id} title={`충족 ${text(g.mandatory_check.passed.map(checkLabel))} / 미충족 ${text(g.mandatory_check.failed.map(checkLabel))} / 미확인 ${text(g.mandatory_check.unknown.map(checkLabel))}`}>{g.mandatory_check.passed.length} / {g.mandatory_check.passed.length + g.mandatory_check.failed.length + g.mandatory_check.unknown.length}</span>) },
  ];
  return <>
    <KvGrid label="협력사 후보 근거" columns={columns} rows={candidates} rowKey={c => c.candidate_code} selectedKey={selected?.candidate_code} fallback="이 요구의 저장된 후보 등급이 없습니다." />
    {selected && <KvBox title={`${selected.candidate_code} 후보 근거`} note="현재 선택 요구 기준">
      {grades(selected).map(g => <div key={g.requirement_id}><p>저장 판정: 요구 v{g.requirement_version ?? "미제공"} · 프로필 v{g.profile_version ?? "미제공"} · 등급 v{g.version ?? "미제공"} · {g.known_at ? formatDateTime(g.known_at) : "판정 시각 미제공"}</p><p>{text(g.reasons.map(reasonLabel))}</p><p>제외 사유: {text(g.exclusion_reasons.map(reasonLabel))}</p><p>필수 충족: {text(g.mandatory_check.passed.map(checkLabel))} · 미충족: {text(g.mandatory_check.failed.map(checkLabel))} · 미확인: {text(g.mandatory_check.unknown.map(checkLabel))}</p></div>)}
      <details><summary>프로필·전체 등급·원문 항목 상세</summary><p>수행 범위: {text(selected.scope.map(value => SCOPE[value] ?? value))}</p><p>인증 전체: {text(selected.certifications)}</p>{grades(selected).map(g => <div key={g.requirement_id}><p>이유 원문: {text(g.reasons)}</p><p>제외 사유 원문: {text(g.exclusion_reasons)}</p><p>필수 확인 원문 · 충족: {text(g.mandatory_check.passed)} / 미충족: {text(g.mandatory_check.failed)} / 미확인: {text(g.mandatory_check.unknown)}</p></div>)}<p>출처: 가상 협력사 · 프로필 판본 {selected.profile_version} · {CONFIRMATION[selected.confirmation_status]}</p><p>사양 속성: {text(selected.spec_facts_attributes.map(value => label(ATTRIBUTE_LABEL, value)))}</p><p>사양 속성 원문: {text(selected.spec_facts_attributes)}</p><p>납기: {selected.lead_time ? selected.lead_time.ex_works ? '공장 출고 기준' : '공장 출고 기준 아님' : '미확인'}</p><p>전체 등급·추가 확인: {GRADE[selected.overall_grade]} · {text(selected.conditions_to_confirm)}</p></details>
    </KvBox>}
  </>;
}
