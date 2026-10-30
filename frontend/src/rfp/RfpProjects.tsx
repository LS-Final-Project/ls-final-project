import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { formatDateTime, PROJECT_STATUS_LABEL } from '../components/tft';
import { KvGrid, KvTitle, type KvColumn } from '../ls-shell/kv';

const ORIGIN_LABEL: Record<string, string> = { synthetic: '합성', demo: '시연', real: '실제' };

const COLUMNS: KvColumn<ProjectSummary>[] = [
  { key: 'name', header: '프로젝트', className: 't', cell: p => <Link to={`/tft/projects/${p.id}`}>{p.name}</Link> },
  { key: 'status', header: '상태', width: 96, className: 'c', cell: p => PROJECT_STATUS_LABEL[p.status] ?? p.status },
  { key: 'owner', header: '책임자', width: 72, className: 'c', cell: p => p.responsible_user_id ? '지정됨' : '미지정' },
  { key: 'created', header: '접수일시', width: 168, className: 'd', cell: p => formatDateTime(p.created_at) },
  { key: 'origin', header: '출처', width: 84, className: 'c', cell: p => ORIGIN_LABEL[p.data_origin] ?? p.data_origin },
];

// 프로젝트 목록(TF0101). 건수 열과 서버 필터는 목록 API에 칸이 오면 COLUMNS에 열을, 제목 줄 아래에 조회조건을 더한다.
export default function RfpProjects() {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const cursor = cursors.at(-1);
  const projects = useQuery({ queryKey: ['projects', mode, cursor], queryFn: () => listProjects(cursor), enabled: access.ready });
  if (!access.ready) return <BusinessGate access={access} title="프로젝트 목록" />;
  const items = projects.data?.data.items ?? [];
  const next = projects.data?.data.next_cursor;
  return <main>
    <KvTitle path="제안 지원" title="프로젝트 목록" code="TF0101">
      <button className="kb primary" disabled={projects.isFetching} onClick={() => projects.refetch()}>조회</button>
    </KvTitle>
    <div className="kv-gridhead">
      <span>{cursors.length}쪽, {items.length}건</span>
      <span className="right">
        <button className="kb" disabled={projects.isFetching || cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>이전</button>{' '}
        <button className="kb" disabled={projects.isFetching || !next} onClick={() => next && setCursors([...cursors, next])}>다음</button>
      </span>
    </div>
    {projects.isError
      ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
      : <KvGrid
        label="프로젝트 목록"
        columns={COLUMNS}
        rows={items}
        rowKey={p => p.id}
        fallback={projects.isPending ? <Loading /> : '프로젝트가 없습니다.'}
      />}
  </main>;
}
