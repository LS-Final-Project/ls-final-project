import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDate, PROJECT_STATUS_LABEL } from '../components/tft';
import { KvFoot, KvGrid, KvTitle, type KvColumn } from '../ls-shell/kv';

const COLUMNS: KvColumn<ProjectSummary>[] = [
  { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, index) => index + 1 },
  { key: 'code', header: '프로젝트', width: 96, cell: p => p.external_id ?? '' },
  { key: 'name', header: '프로젝트명', className: 't', cell: p => <Link to={`/tft/projects/${p.id}`}>{p.name}</Link> },
  { key: 'status', header: '진행', width: 96, className: 'c', cell: p => PROJECT_STATUS_LABEL[p.status] ?? p.status },
  { key: 'sales', header: '담당', width: 72, className: 'c', cell: p => p.sales_user_id ? '지정' : '미지정' },
  { key: 'owner', header: '책임자', width: 72, className: 'c', cell: p => p.responsible_user_id ? '지정' : '미지정' },
  { key: 'created', header: '접수일', width: 104, className: 'd', cell: p => formatDate(p.created_at.slice(0, 10)) },
];

// 접수 및 요구 검토 TF0101의 프로젝트 목록(TF_K_Intake). 고객·판단·건수 열과 필터는 목록 API에 칸이 오면 COLUMNS에 열을,
// 제목 줄 아래에 KvCond를 더한다. 프로젝트 상태판과 문서 표는 ②에서 붙인다.
export default function RfpProjects() {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const cursor = cursors.at(-1);
  const projects = useQuery({ queryKey: ['projects', mode, cursor], queryFn: () => listProjects(cursor), enabled: access.ready });
  if (!access.ready) return <BusinessGate access={access} title="접수 및 요구 검토" />;
  const items = projects.data?.data.items ?? [];
  const next = projects.data?.data.next_cursor;
  return <main>
    <KvTitle path="제안 지원" title="접수 및 요구 검토" code="TF0101">
      <button className="kb primary" disabled={projects.isFetching} onClick={() => projects.refetch()}>조회</button>
    </KvTitle>
    <div className="kv-gridhead"><span>내 프로젝트 {items.length}건, 접수일 내림차순</span></div>
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
