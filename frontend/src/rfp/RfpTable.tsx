import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getApiMode } from '../api/client';
import {
  getComparison, getRequirement, listDocuments, listProposals, listRequirements,
  type Comparison, type Necessity, type RequirementFilters, type RequirementStatus, type RequirementSummary,
} from '../api/rfp';
import { getProject } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvBox, KvCond, KvCondField, KvCondRow, KvField, KvFoot, KvGrid, KvNext, KvSplit, KvTitle, type KvColumn } from '../ls-shell/kv';
import { label, LIFECYCLE_LABEL, MATCH_LABEL, NECESSITY_LABEL, OPERATOR_LABEL, OWNER_LABEL, PATH_LABEL, STATUS_LABEL } from './labels';
import ProposalForm from './ProposalForm';

type Candidate = Comparison['candidates'][number];
const CANDIDATES: KvColumn<Candidate>[] = [
  { key: 'product', header: '자사 제품', width: 110, cell: c => c.product_code },
  { key: 'variant', header: '변형', width: 48, className: 'c', cell: c => c.variant ?? '' },
  { key: 'scope', header: '수행 범위', width: 72, cell: c => c.scope.join(', ') },
  { key: 'status', header: '상태', width: 64, className: 'c', cell: c => label(LIFECYCLE_LABEL, c.lifecycle_status) },
  { key: 'value', header: '자사 값', cell: c => [c.attribute_match.our_value, c.attribute_match.our_unit].filter(Boolean).join(' ') || '없음' },
  { key: 'comparable', header: '비교 가능', width: 64, className: 'c', cell: c => c.attribute_match.comparable ? '예' : '아니오' },
  { key: 'result', header: '결과', width: 64, className: 'c', cell: c => label(MATCH_LABEL, c.attribute_match.result) },
  { key: 'evidence', header: '근거', width: 170, cell: c => c.attribute_match.evidence.map(e => [e.document, e.clause].filter(Boolean).join(' ')).join(', ') || '근거 없음' },
];

function Detail({ projectId, id, proposing, onPropose, onClose }: {
  projectId: string;
  id: string;
  proposing: boolean;
  onPropose: () => void;
  onClose: () => void;
}) {
  const mode = getApiMode();
  const client = useQueryClient();
  const requirement = useQuery({ queryKey: ['rfp-requirement', mode, id], queryFn: () => getRequirement(id) });
  const comparison = useQuery({ queryKey: ['rfp-comparison', mode, id], queryFn: () => getComparison(id) });
  const pending = useQuery({
    queryKey: ['rfp-proposals', mode, projectId, 'requirement', id],
    queryFn: () => listProposals(projectId, { requirement_id: id, status: 'PENDING' }),
  });
  if (requirement.isPending) return <Loading />;
  if (requirement.isError) return <ErrorState error={requirement.error} onRetry={() => requirement.refetch()} />;
  const data = requirement.data.data;
  const { spec, judgement, origin } = data;
  const trace = comparison.data?.data.search_trace;
  const title = `${data.code} ${spec.attribute ?? ''}`.trim();
  const written = judgement.provenance_kind === 'ai_draft' ? 'AI 작성' : '직접 입력';
  return <>
    <KvSplit columns="1.6fr 1fr">
      <KvBox title={`자사 비교, ${title}`} note={written}>
        {comparison.isError
          ? <ErrorState error={comparison.error} onRetry={() => comparison.refetch()} />
          : <KvGrid
            label="자사 비교"
            columns={CANDIDATES}
            rows={comparison.data?.data.candidates ?? []}
            rowKey={c => `${c.product_code}:${c.variant ?? ''}`}
            fallback={comparison.isPending ? <Loading /> : trace?.search_failed ? '후보 검색이 실패했습니다.' : '비교할 자사 후보가 없습니다.'}
          />}
        {trace && <div className="kv-sub" style={{ margin: '8px 0 0' }}>검색: 문서 {trace.documents_searched}건, {trace.search_failed ? '실패' : '실패 없음'}. 가격과 표준 납기는 비교하지 않음</div>}
      </KvBox>
      <KvBox title={`판단, ${data.code}`} note={written}>
        <div className="kv-field"><span>상태</span><span className="ro">{label(STATUS_LABEL, judgement.status)}{data.needs_rereview && <span className="kv-flags"><i className="rr">재검토</i></span>}</span></div>
        <div className="kv-field"><span>사유</span><div className="kv-text" style={{ minHeight: 0 }}>{judgement.reason}</div></div>
        {judgement.unresolved.length > 0 && <KvField label="미확인">{judgement.unresolved.join(', ')}</KvField>}
        <KvField label="다음 담당">{label(OWNER_LABEL, judgement.next_owner_field) || '없음'}</KvField>
        {(pending.data?.data.items.length ?? 0) > 0 && <KvField label="승인 대기">
          <Link to={`/tft/projects/${projectId}/table/approvals`}>{pending.data?.data.items.length}건</Link>
        </KvField>}
        <div className="kv-field"><span>원문</span><div className="kv-text" lang="en" style={{ minHeight: 0 }}>{[origin.clause_no, origin.page != null ? `p.${origin.page}` : ''].filter(Boolean).join(' ')} {origin.quote}</div></div>
        <div className="kv-actions"><button className="kb" onClick={onPropose}>변경 제안</button></div>
      </KvBox>
    </KvSplit>
    {proposing && <ProposalForm
      key={id}
      projectId={projectId}
      requirement={data}
      onCancel={onClose}
      onSaved={() => { onClose(); client.invalidateQueries({ queryKey: ['rfp-proposals', mode] }); }}
      onReload={() => { requirement.refetch(); comparison.refetch(); }}
    />}
  </>;
}

const SIZES = [50, 100];

// 자사 대응표(TF0102). 보드에 있어도 API에 없는 값(다음 담당 필터, 작성 열)은 넣지 않는다.
export default function RfpTable() {
  const { projectId = '' } = useParams();
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const [filters, setFilters] = useState<RequirementFilters>({});
  const [equipment, setEquipment] = useState('');
  const [task, setTask] = useState('');
  const [size, setSize] = useState(SIZES[0]);
  const [selected, setSelected] = useState<string>();
  const [proposing, setProposing] = useState(false);
  const enabled = access.ready && projectId !== '';
  const project = useQuery({ queryKey: ['projects', mode, projectId], queryFn: () => getProject(projectId), enabled });
  const documents = useQuery({ queryKey: ['rfp-documents', mode, projectId], queryFn: () => listDocuments(projectId), enabled });
  const waiting = useQuery({
    queryKey: ['rfp-proposals', mode, projectId, 'PENDING'],
    queryFn: () => listProposals(projectId, { status: 'PENDING' }),
    enabled,
  });
  const requirements = useInfiniteQuery({
    queryKey: ['rfp-requirements', mode, projectId, filters, size],
    queryFn: ({ pageParam }) => listRequirements(projectId, filters, size, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: last => last.data.next_cursor ?? undefined,
    // 조건을 바꾸는 동안 표를 비우지 않는다. 행 단추가 사라지면 누르던 클릭이 유실된다.
    placeholderData: keepPreviousData,
    enabled,
  });
  if (!access.ready) return <BusinessGate access={access} title="자사 대응표" />;
  const filter = (change: RequirementFilters) => {
    const next = { ...filters, ...change };
    // 값이 그대로면 선택과 입력 중인 제안을 지키고 다시 부르지 않는다.
    if (JSON.stringify(next) === JSON.stringify(filters)) return;
    setFilters(next);
    setSelected(undefined);
    setProposing(false);
  };
  const applyText = () => filter({ equipment: equipment.trim() || undefined, task: task.trim() || undefined });
  const tri = (value: string) => value === '' ? undefined : value === 'true';
  const triValue = (value: boolean | undefined) => value === undefined ? '' : String(value);
  const items = requirements.data?.pages.flatMap(page => page.data.items) ?? [];
  const detail = project.data?.data;
  // 전체 건수는 프로젝트 합계라 조건을 걸지 않았을 때만 뜻이 맞다.
  const unfiltered = Object.values(filters).every(value => value === undefined);
  // 승인은 담당 영업·책임자만 된다. 부서 검토 계정은 단추를 막고, 나머지는 서버가 다시 검사한다.
  const roles = access.user.data?.data.roles;
  const canDecide = !roles || roles.includes('sales') || roles.includes('manager');
  const pendingCount = waiting.data?.data.items.length;
  const approvals = `승인 대기${pendingCount === undefined ? '' : ` ${pendingCount}`}`;
  const columns: KvColumn<RequirementSummary>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_r, index) => index + 1 },
    { key: 'code', header: '요구', width: 72, cell: r => <button className="kb sm" style={{ padding: '0 6px' }} aria-pressed={r.requirement_id === selected} onClick={() => { setSelected(r.requirement_id); setProposing(false); }}>{r.code}</button> },
    { key: 'equipment', header: '장비', cell: r => r.equipment ?? '' },
    { key: 'task', header: '작업', width: 96, cell: r => r.task ?? '' },
    { key: 'attribute', header: '속성', cell: r => r.attribute ?? '' },
    { key: 'value', header: '요구값', cell: r => r.value_text ?? '값 없음' },
    { key: 'necessity', header: '성격', width: 68, className: 'c', cell: r => label(NECESSITY_LABEL, r.necessity) },
    { key: 'status', header: '상태', width: 84, className: 'c', cell: r => label(STATUS_LABEL, r.status) },
    { key: 'path', header: '보완 경로', width: 100, cell: r => r.path === 'NONE' ? '' : label(PATH_LABEL, r.path) },
    { key: 'questions', header: '질문', width: 44, className: 'c', cell: r => r.open_questions || '' },
    { key: 'rereview', header: '재검토', width: 56, className: 'c', cell: r => r.needs_rereview ? '재검토' : '' },
  ];
  return <main>
    <KvTitle path="제안 지원" title="자사 대응표" code="TF0102">
      <button className="kb primary" disabled={!selected} title={selected ? undefined : '요구를 먼저 고르세요'} onClick={() => setProposing(true)}>변경 제안</button>
      {canDecide
        ? <Link className="kb" to={`/tft/projects/${projectId}/table/approvals`}>{approvals}</Link>
        : <button className="kb" disabled title="담당 또는 책임자만 승인합니다">{approvals}</button>}
      <button className="kb" disabled={requirements.isFetching} onClick={() => { applyText(); requirements.refetch(); }}>조회</button>
    </KvTitle>
    <KvNext>
      {detail
        ? `${detail.name}, 요구 ${detail.requirements_count}건, 미해결 필수 ${detail.unresolved_mandatory_count}, 재검토 ${detail.requirement_counts.needs_rereview}`
        : project.isError ? '프로젝트 정보를 불러오지 못했습니다' : '불러오는 중'}
      {pendingCount ? `. 다음 승인 대기 ${pendingCount}건 처리` : ''}. 승인하면 요구 새 판본, 연결된 비교와 초안 문장에 재검토 표시
    </KvNext>
    <KvCond onSearch={applyText}>
      <KvCondRow>
        <KvCondField label="문서 판본"><select aria-label="문서 판본" value={filters.document_version_id ?? ''} onChange={event => filter({ document_version_id: event.target.value || undefined })}>
          <option value="">전체</option>
          {documents.data?.data.items.map(d => <option key={d.document_version_id} value={d.document_version_id}>{d.title}</option>)}
        </select></KvCondField>
        <KvCondField label="장비"><input aria-label="장비" type="text" value={equipment} onChange={event => setEquipment(event.target.value)} /></KvCondField>
        <KvCondField label="작업"><input aria-label="작업" type="text" value={task} onChange={event => setTask(event.target.value)} /></KvCondField>
        <KvCondField label="성격"><select aria-label="성격" value={filters.necessity ?? ''} onChange={event => filter({ necessity: (event.target.value || undefined) as Necessity | undefined })}>
          <option value="">전체</option>
          {Object.entries(NECESSITY_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select></KvCondField>
      </KvCondRow>
      <KvCondRow columns={3}>
        <KvCondField label="상태"><select aria-label="상태" value={filters.status ?? ''} onChange={event => filter({ status: (event.target.value || undefined) as RequirementStatus | undefined })}>
          <option value="">전체</option>
          {Object.entries(STATUS_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select></KvCondField>
        <KvCondField label="재검토"><select aria-label="재검토" value={triValue(filters.needs_rereview)} onChange={event => filter({ needs_rereview: tri(event.target.value) })}>
          <option value="">전체</option><option value="true">필요</option><option value="false">없음</option>
        </select></KvCondField>
        <KvCondField label="열린 질문"><select aria-label="열린 질문" value={triValue(filters.has_open_question)} onChange={event => filter({ has_open_question: tri(event.target.value) })}>
          <option value="">전체</option><option value="true">있음</option><option value="false">없음</option>
        </select></KvCondField>
      </KvCondRow>
      <button type="submit" hidden>검색</button>
    </KvCond>
    <div className="kv-gridhead"><span>요구 {items.length}건 표시, 요구 번호를 누르면 아래에 자사 비교와 판단</span></div>
    {requirements.isError
      ? <ErrorState error={requirements.error} onRetry={() => requirements.refetch()} />
      : <>
        <KvGrid
          label="자사 대응표"
          columns={columns}
          rows={items}
          rowKey={r => r.requirement_id}
          selectedKey={selected}
          fallback={requirements.isPending ? <Loading /> : '조건에 맞는 요구가 없습니다.'}
        />
        <KvFoot
          count={`표시 ${items.length}건${unfiltered && detail ? ` / 조회 ${detail.requirements_count}건` : ''}`}
          more={requirements.hasNextPage
            ? <button className="kb" disabled={requirements.isFetchingNextPage} onClick={() => requirements.fetchNextPage()}>{size}건 더 보기</button>
            : '마지막입니다'}
          size={<select aria-label="한 번에" value={size} onChange={event => setSize(Number(event.target.value))}>
            {SIZES.map(value => <option key={value} value={value}>{value}건</option>)}
          </select>}
        />
      </>}
    {selected && <Detail key={selected} projectId={projectId} id={selected} proposing={proposing} onPropose={() => setProposing(true)} onClose={() => setProposing(false)} />}
  </main>;
}
