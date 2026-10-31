import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects, type ProjectFilters, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDate } from '../components/tft';
import { KvCond, KvCondField, KvCondRow, KvFoot, KvGrid, KvTitle, type KvColumn } from '../ls-shell/kv';
import { JUDGEMENT_LABEL, label, OUTPUT_LABEL } from './labels';

const count = (value: number, ready: boolean) => ready ? value : '';
const COLUMNS: KvColumn<ProjectSummary>[] = [
  { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, index) => index + 1 },
  { key: 'code', header: '프로젝트', width: 96, cell: p => p.external_id ?? '' },
  { key: 'name', header: '프로젝트명', className: 't', cell: p => <Link to={`/tft/projects/${p.id}`}>{p.name}</Link> },
  { key: 'client', header: '고객', width: 104, cell: p => p.client_name ?? '' },
  { key: 'output', header: '진행', width: 84, className: 'c', cell: p => label(OUTPUT_LABEL, p.output_status) },
  { key: 'judgement', header: '판단', width: 104, className: 'c', cell: p => label(JUDGEMENT_LABEL, p.judgement_status) },
  // 요구가 없는 접수 단계는 0이 아니라 빈칸이다(아직 셀 것이 없다).
  { key: 'requirements', header: '요구', width: 48, className: 'n', cell: p => p.requirements_count || '없음' },
  { key: 'unresolved', header: '미해결 필수', width: 76, className: 'n', cell: p => count(p.unresolved_mandatory_count, p.requirements_count > 0) },
  { key: 'pending', header: '승인 대기', width: 64, className: 'n', cell: p => count(p.pending_proposals_count, p.requirements_count > 0) },
  { key: 'questions', header: '열린 질문', width: 64, className: 'n', cell: p => count(p.open_questions_count, p.requirements_count > 0) },
  { key: 'sales', header: '담당', width: 60, className: 'c', cell: p => p.sales_user_id ? '지정' : '미지정' },
  { key: 'owner', header: '책임자', width: 60, className: 'c', cell: p => p.responsible_user_id ? '지정' : '미지정' },
  { key: 'created', header: '접수일', width: 96, className: 'd', cell: p => formatDate(p.created_at.slice(0, 10)) },
];

// 접수 및 요구 검토 TF0101의 프로젝트 목록(TF_K_Intake). 프로젝트 상태판과 문서 표는 ②에서 붙인다.
export default function RfpProjects() {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const [filters, setFilters] = useState<ProjectFilters>({});
  const [text, setText] = useState('');
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const cursor = cursors.at(-1);
  const projects = useQuery({ queryKey: ['projects', mode, filters, cursor], queryFn: () => listProjects(filters, cursor), enabled: access.ready, placeholderData: keepPreviousData });
  if (!access.ready) return <BusinessGate access={access} title="접수 및 요구 검토" />;
  const filter = (change: ProjectFilters) => { setFilters({ ...filters, ...change }); setCursors([undefined]); };
  // 서버 검색어는 2~100자다. 한 글자는 보내지 않고 알린다.
  const search = text.trim();
  const shortSearch = search.length === 1;
  const items = projects.data?.data.items ?? [];
  const next = projects.data?.data.next_cursor;
  return <main>
    <KvTitle path="제안 지원" title="접수 및 요구 검토" code="TF0101">
      <button className="kb primary" disabled={projects.isFetching || shortSearch} onClick={() => { if (search !== (filters.q ?? '')) filter({ q: search || undefined }); else projects.refetch(); }}>조회</button>
    </KvTitle>
    <KvCond onSearch={() => { if (!shortSearch) filter({ q: search || undefined }); }}>
      <KvCondRow columns={3}>
        <KvCondField label="진행"><select aria-label="진행" value={filters.output_status ?? ''} onChange={event => filter({ output_status: event.target.value || undefined })}>
          <option value="">전체</option>
          {Object.entries(OUTPUT_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select></KvCondField>
        <KvCondField label="판단"><select aria-label="판단" value={filters.judgement_status ?? ''} onChange={event => filter({ judgement_status: event.target.value || undefined })}>
          <option value="">전체</option>
          {Object.entries(JUDGEMENT_LABEL).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select></KvCondField>
        <KvCondField label="검색어"><input aria-label="검색어" type="text" maxLength={100} value={text} onChange={event => setText(event.target.value)} /></KvCondField>
      </KvCondRow>
      <button type="submit" hidden>검색</button>
    </KvCond>
    <div className="kv-gridhead"><span>{shortSearch ? '검색어는 두 글자 이상 입력하세요.' : `내 프로젝트 ${items.length}건, 접수일 내림차순`}</span></div>
    {projects.isError
      ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
      : <KvGrid
        label="접수 및 요구 검토"
        columns={COLUMNS}
        rows={items}
        rowKey={p => p.id}
        fallback={projects.isPending ? <Loading /> : '프로젝트가 없습니다.'}
      />}
    <KvFoot
      count={`${cursors.length}쪽, ${items.length}건`}
      more={<>
        <button className="kb" disabled={projects.isFetching || cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>이전</button>
        <button className="kb" disabled={projects.isFetching || !next} onClick={() => next && setCursors([...cursors, next])}>다음</button>
      </>}
    />
  </main>;
}
