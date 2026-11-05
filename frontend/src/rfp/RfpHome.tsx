import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects, type ProjectSummary } from '../api/tft';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvGrid, KvTitle, type KvColumn } from '../ls-shell/kv';
import { dueText, progressOf } from './RfpList';
import { useNames } from './names';
import './RfpWorkspace.css';

// 프로젝트별 오늘 할 일: 결정 남은 수와 마감, 회신 기다리는 질의, 승인 대기.
export function todayTasks(p: ProjectSummary, today = new Date()): string[] {
  if (p.requirements_count === 0) return ['분석 시작 전'];
  const due = p.submission_due_at ? dueText(p.submission_due_at, today).replace(/^.*\((.*)\)$/, '$1').replace('남음', '전') : '';
  return [
    (p.decisions_remaining ?? 0) > 0 && `결정 ${p.decisions_remaining}건 남음${due ? ` (마감 ${due})` : ''}`,
    p.open_questions_count > 0 && `회신 기다리는 질의 ${p.open_questions_count}건`,
    p.pending_proposals_count > 0 && `승인 대기 ${p.pending_proposals_count}건`,
  ].filter((x): x is string => !!x);
}

type Owner = { sales: string; projects: number; left: number; wait: number };
const ownerColumns = (name: (id?: string | null) => string): KvColumn<Owner>[] => [
  { key: 'sales', header: '담당자', cell: o => name(o.sales) || '미지정' },
  { key: 'projects', header: '프로젝트', width: 80, className: 'c', cell: o => o.projects },
  { key: 'left', header: '결정 남은 것', width: 100, className: 'c', cell: o => o.left },
  { key: 'wait', header: '확인 대기', width: 80, className: 'c', cell: o => o.wait },
];

const recentColumns: KvColumn<ProjectSummary>[] = [
  { key: 'name', header: '프로젝트', cell: p => <><Link to={`/tft/projects/${p.id}`}>{p.external_id}</Link> {p.name}</> },
  { key: 'state', header: '진행 상태', width: 180, cell: p => progressOf(p).text },
  { key: 'due', header: '제안 마감', width: 150, cell: p => dueText(p.submission_due_at) },
];

// 업무 홈 TF0001: 프로젝트를 넘나드는 오늘 할 일만 둔다. 책임자에게는 담당자별 진행을 더한다.
export default function RfpHome() {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const actorId = access.user.data?.data.id;
  const projects = useQuery({ queryKey: ['projects', mode, actorId, {}, 1], queryFn: () => listProjects({}, undefined, 1), enabled: access.ready });
  const items = projects.data?.data.items ?? [];
  const name = useNames(items.flatMap(p => [p.sales_user_id, p.responsible_user_id]));
  if (!access.ready) return <BusinessGate access={access} title="업무 홈" />;
  const tasks = items.flatMap(p => todayTasks(p).map(task => ({ p, task })));
  const led = items.filter(p => !!actorId && p.responsible_user_id === actorId);
  const owners = [...new Set(led.map(p => p.sales_user_id ?? ''))].map(sales => {
    const mine = led.filter(p => (p.sales_user_id ?? '') === sales);
    return { sales, projects: mine.length, left: mine.reduce((n, p) => n + (p.decisions_remaining ?? 0), 0), wait: mine.reduce((n, p) => n + (p.review_pending ?? 0), 0) };
  });
  return <main className="rfp-ui">
    <KvTitle path="제안 지원" title="업무 홈" code="TF0001"><Link className="kb primary" to="/tft/projects/new">새 RFP</Link></KvTitle>
    {projects.isError ? <ErrorState error={projects.error} onRetry={() => projects.refetch()} /> : projects.isPending ? <Loading /> : <>
      <section className="kv-box"><h2>오늘 할 일<small>{tasks.length}건</small></h2>
        <div className="body" style={{ padding: 0 }}>
          {tasks.length === 0 ? <p style={{ padding: '10px 12px', margin: 0 }}>오늘 할 일이 없습니다.</p>
            : <ul className="rfp-today">{tasks.map(({ p, task }) => <li key={`${p.id}:${task}`}>
              <span>{task}</span><span className="muted">{p.external_id} {p.name}</span>
              <Link className="kb" to={`/tft/projects/${p.id}`}>{progressOf(p, !!actorId && p.responsible_user_id === actorId).action}</Link>
            </li>)}</ul>}
        </div>
      </section>
      {led.length > 0 && <section className="kv-box"><h2>담당자별 진행</h2><div className="body" style={{ padding: 0 }}>
        <KvGrid label="담당자별 진행" columns={ownerColumns(name)} rows={owners} rowKey={o => o.sales} fallback="담당 프로젝트가 없습니다." />
      </div></section>}
      <section className="kv-box"><h2>최근 프로젝트<small><Link to="/tft/projects">전체 목록</Link></small></h2><div className="body" style={{ padding: 0 }}>
        <KvGrid label="최근 프로젝트" columns={recentColumns} rows={items.slice(0, 5)} rowKey={p => p.id} fallback="프로젝트가 없습니다." />
      </div></section>
    </>}
  </main>;
}
