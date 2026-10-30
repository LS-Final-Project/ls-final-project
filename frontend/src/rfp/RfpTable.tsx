import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import {
  getComparison, getRequirement, listRequirements,
  type Comparison, type Necessity, type RequirementFilters, type RequirementStatus, type RequirementSummary,
} from '../api/rfp';
import { getProject } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvBox, KvField, KvGrid, KvNext, KvSplit, KvTitle, type KvColumn } from '../ls-shell/kv';
import { label, MATCH_LABEL, NECESSITY_LABEL, OPERATOR_LABEL, OWNER_LABEL, PATH_LABEL, STATUS_LABEL } from './labels';
import ProposalPanel from './ProposalPanel';

type Candidate = Comparison['candidates'][number];
const CANDIDATES: KvColumn<Candidate>[] = [
  { key: 'product', header: '제품', className: 't', cell: c => c.product_code },
  { key: 'value', header: '자사 값', width: 110, cell: c => [c.attribute_match.our_value, c.attribute_match.our_unit].filter(Boolean).join(' ') || '-' },
  { key: 'result', header: '결과', width: 76, className: 'c', cell: c => label(MATCH_LABEL, c.attribute_match.result) },
  { key: 'evidence', header: '근거', cell: c => c.attribute_match.evidence.map(e => [e.document, e.clause].filter(Boolean).join(' ')).join(', ') || '근거 없음' },
];

function Detail({ projectId, id, canDecide }: { projectId: string; id: string; canDecide: boolean }) {
  const mode = getApiMode();
  const requirement = useQuery({ queryKey: ['rfp-requirement', mode, id], queryFn: () => getRequirement(id) });
  const comparison = useQuery({ queryKey: ['rfp-comparison', mode, id], queryFn: () => getComparison(id) });
  if (requirement.isPending) return <Loading />;
  if (requirement.isError) return <ErrorState error={requirement.error} onRetry={() => requirement.refetch()} />;
  const data = requirement.data.data;
  const { spec, judgement, origin } = data;
  const value = [spec.value, spec.unit, OPERATOR_LABEL[spec.operator]].filter(Boolean).join(' ') || '값 없음';
  return <KvSplit columns="1.4fr 1fr">
    <div>
      <KvBox title={`요구 ${data.code}`} note={data.needs_rereview ? '재검토 필요' : undefined}>
        <KvField label="원문">{origin.clause_no ? `${origin.clause_no} ` : ''}{origin.page != null ? `p.${origin.page} ` : ''}<q lang="en">{origin.quote}</q></KvField>
        <KvField label="요구값">{value}</KvField>
        <KvField label="판단">{label(STATUS_LABEL, judgement.status)} ({judgement.provenance_kind === 'ai_draft' ? 'AI 작성' : '사람 확인'})</KvField>
        <KvField label="사유">{judgement.reason}</KvField>
        <KvField label="다음 담당">{label(OWNER_LABEL, judgement.next_owner_field)}</KvField>
        {judgement.unresolved.length > 0 && <KvField label="미해결">{judgement.unresolved.join(', ')}</KvField>}
      </KvBox>
      <div className="kv-gridhead"><span>자사 비교</span></div>
      {comparison.isError
        ? <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} />
        : <KvGrid
          label="자사 비교"
          columns={CANDIDATES}
          rows={comparison.data?.data.candidates ?? []}
          rowKey={c => `${c.product_code}:${c.variant ?? ''}`}
          fallback={comparison.isPending ? <Loading /> : comparison.data?.data.search_trace.search_failed ? '후보 검색이 실패했습니다.' : '비교할 자사 후보가 없습니다.'}
        />}
    </div>
    <ProposalPanel projectId={projectId} requirement={data} canDecide={canDecide} />
  </KvSplit>;
}

// 자사 대응표(TF0102). 요구를 조회조건으로 거르고, 고른 요구의 근거·비교·변경 제안을 아래에 보여 준다.
export default function RfpTable() {
  const { projectId = '' } = useParams();
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const [filters, setFilters] = useState<RequirementFilters>({});
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const [selected, setSelected] = useState<string>();
  const cursor = cursors.at(-1);
  const enabled = access.ready && projectId !== '';
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled });
  const requirements = useQuery({
    queryKey: ['rfp-requirements', mode, projectId, filters, cursor],
    queryFn: () => listRequirements(projectId, filters, cursor),
    enabled,
  });
  if (!access.ready) return <BusinessGate access={access} title="자사 대응표" />;
  const filter = (change: RequirementFilters) => {
    setFilters({ ...filters, ...change });
    setCursors([undefined]);
    setSelected(undefined);
  };
  const items = requirements.data?.data.items ?? [];
  const next = requirements.data?.data.next_cursor;
  // 승인은 담당 영업·책임자만 된다. 부서 검토 계정에는 버튼을 내지 않고, 나머지는 서버가 다시 검사한다.
  const roles = access.user.data?.data.roles;
  const canDecide = !roles || roles.includes('sales') || roles.includes('manager');
  const columns: KvColumn<RequirementSummary>[] = [
    { key: 'code', header: '번호', width: 76, cell: r => <button className="kb sm" style={{ padding: '0 6px' }} aria-pressed={r.requirement_id === selected} onClick={() => setSelected(r.requirement_id)}>{r.code}</button> },
    { key: 'item', header: '장비·과업', className: 't', cell: r => r.equipment ?? r.task ?? '-' },
    { key: 'attribute', header: '항목', cell: r => r.attribute ?? '-' },
    { key: 'value', header: '요구값', cell: r => r.value_text ?? '-' },
    { key: 'necessity', header: '필수 여부', width: 84, className: 'c', cell: r => label(NECESSITY_LABEL, r.necessity) },
    { key: 'status', header: '상태', width: 84, className: 'c', cell: r => label(STATUS_LABEL, r.status) },
    { key: 'path', header: '보완 경로', width: 112, className: 'c', cell: r => label(PATH_LABEL, r.path) },
    { key: 'questions', header: '질문', width: 48, className: 'c', cell: r => r.open_questions || '-' },
    { key: 'rereview', header: '재검토', width: 56, className: 'c', cell: r => r.needs_rereview ? '필요' : '-' },
  ];
  return <main>
    <KvTitle path="제안 지원" title="자사 대응표" code="TF0102">
      <button className="kb primary" disabled={requirements.isFetching} onClick={() => requirements.refetch()}>조회</button>
      <Link className="kb" to={`/tft/projects/${projectId}`}>프로젝트</Link>
    </KvTitle>
    <KvNext label="프로젝트">{project.data?.data.name ?? (project.isError ? '프로젝트 정보를 불러오지 못했습니다.' : '불러오는 중')}</KvNext>
    <div className="kv-gridhead">
      <label>상태 <select aria-label="상태" value={filters.status ?? ''} onChange={event => filter({ status: (event.target.value || undefined) as RequirementStatus | undefined })}>
        <option value="">전체</option>
        {Object.entries(STATUS_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
      </select></label>
      <label>필수 여부 <select aria-label="필수 여부" value={filters.necessity ?? ''} onChange={event => filter({ necessity: (event.target.value || undefined) as Necessity | undefined })}>
        <option value="">전체</option>
        {Object.entries(NECESSITY_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
      </select></label>
      <label><input type="checkbox" style={{ display: 'inline-block', width: 'auto', minHeight: 0, margin: '0 4px 0 0', verticalAlign: 'middle' }} checked={!!filters.needs_rereview} onChange={event => filter({ needs_rereview: event.target.checked || undefined })} /> 재검토만</label>
      <label><input type="checkbox" style={{ display: 'inline-block', width: 'auto', minHeight: 0, margin: '0 4px 0 0', verticalAlign: 'middle' }} checked={!!filters.has_open_question} onChange={event => filter({ has_open_question: event.target.checked || undefined })} /> 열린 질문만</label>
      <span className="right">
        {cursors.length}쪽, {items.length}건{' '}
        <button className="kb" disabled={requirements.isFetching || cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>이전</button>{' '}
        <button className="kb" disabled={requirements.isFetching || !next} onClick={() => next && setCursors([...cursors, next])}>다음</button>
      </span>
    </div>
    {requirements.isError
      ? <ErrorState error={requirements.error} onRetry={() => requirements.refetch()} />
      : <KvGrid
        label="자사 대응표"
        columns={columns}
        rows={items}
        rowKey={r => r.requirement_id}
        selectedKey={selected}
        fallback={requirements.isPending ? <Loading /> : '조건에 맞는 요구가 없습니다.'}
      />}
    {selected
      ? <Detail key={selected} projectId={projectId} id={selected} canDecide={canDecide} />
      : <KvNext label="안내">표에서 요구 번호를 누르면 근거와 비교, 변경 제안을 볼 수 있습니다.</KvNext>}
  </main>;
}
