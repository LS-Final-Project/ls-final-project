import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects, type ProjectFilters, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvGrid, KvTitle, type KvColumn } from '../ls-shell/kv';
import { ProjectPagination } from './ProjectPagination';
import { useNames } from './names';

// 진행 상태: 결정 남은 수(decisions_remaining)와 책임자 확인 대기(review_pending)로 말한다.
export function progressOf(p: ProjectSummary, responsible = false) {
  if (p.requirements_count === 0) return { text: '분석 대기', action: '분석 시작' };
  const wait = responsible ? `, 확인 대기 ${p.review_pending ?? 0}` : '';
  if ((p.decisions_remaining ?? 0) > 0) return { text: `결정 ${p.decisions_remaining}건 남음${wait}`, action: responsible ? '확인하기' : '결정 이어서 하기' };
  if (responsible && (p.review_pending ?? 0) > 0) return { text: `확인 대기 ${p.review_pending}건`, action: '확인하기' };
  return { text: '출력 가능', action: '출력 보기' };
}

// 제안 마감: 날짜와 남은 날수(한국 날짜). 입력 전이면 '─'.
export function dueText(due: string | null | undefined, today = new Date()) {
  if (!due) return '─';
  const kst = new Date(today.getTime() + 9 * 3_600_000).toISOString().slice(0, 10);
  const days = Math.round((Date.parse(due) - Date.parse(kst)) / 86_400_000);
  return `${due} (${days < 0 ? `${-days}일 지남` : days === 0 ? '오늘' : `${days}일 남음`})`;
}

// RFP 검토 목록: 새 RFP와 이어서 할 프로젝트.
export default function RfpList() {
  const mode = getApiMode();
  const navigate = useNavigate();
  const access = useBusinessAccess('tft');
  const [filters, setFilters] = useState<ProjectFilters>({});
  const [text, setText] = useState('');
  const [page, setPage] = useState(1);
  const projects = useQuery({ queryKey: ['projects', mode, access.user.data?.data.id, filters, page], queryFn: () => listProjects(filters, undefined, page), enabled: access.ready, placeholderData: keepPreviousData });
  const items = projects.data?.data.items ?? [];
  const name = useNames(items.map(p => p.responsible_user_id));
  if (!access.ready) return <BusinessGate access={access} title="RFP 검토" />;
  const mine = (p: ProjectSummary) => !!access.user.data?.data.id && p.responsible_user_id === access.user.data.data.id;
  const columns: KvColumn<ProjectSummary>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, i) => (page - 1) * 10 + i + 1 },
    { key: 'name', header: '프로젝트', className: 't', cell: p => <Link to={`/tft/projects/${p.id}`}>{p.external_id} {p.name}</Link> },
    { key: 'progress', header: '진행 상태', width: 190, cell: p => progressOf(p, mine(p)).text },
    { key: 'deadline', header: '제안 마감', width: 150, className: 'c', cell: p => dueText(p.submission_due_at) },
    { key: 'owner', header: '책임자', width: 104, className: 'c', cell: p => name(p.responsible_user_id) || '미지정' },
    { key: 'next', header: '다음 행동', width: 110, className: 'c', cell: p => <Link className="kb" to={`/tft/projects/${p.id}`}>{progressOf(p, mine(p)).action}</Link> },
  ];
  const search = text.trim();
  return <main>
    <KvTitle path="제안 지원" title="RFP 검토" code="TF0101"><Link className="kb primary" to="/tft/projects/new">새 RFP</Link></KvTitle>
    <form className="rfp-project-search" aria-label="프로젝트 검색" onSubmit={event => { event.preventDefault(); if (search.length !== 1) { setFilters({ ...filters, q: search || undefined }); setPage(1); } }}>
      <input aria-label="검색어" value={text} maxLength={100} placeholder="번호 또는 이름, 2자 이상" onChange={event => setText(event.target.value)} />
      <button className="kb" type="submit" disabled={search.length === 1}>검색</button>
    </form>
    {projects.isError ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
      : <KvGrid label="RFP 검토" columns={columns} rows={items} rowKey={p => p.id} onRow={p => navigate(`/tft/projects/${p.id}`)} fallback={projects.isPending ? <Loading /> : '프로젝트가 없습니다.'} />}
    {!projects.isError && <ProjectPagination page={page} total={projects.data?.data.total} busy={projects.isFetching} onPage={setPage} />}
  </main>;
}
