import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listProjects } from '../api/tft';
import { Empty, ErrorState, Loading, Preparing } from '../components/states';
import { PROJECT_STATUS_LABEL, StatusBadge } from '../components/tft';

export default function Projects() {
  const mode = getApiMode();
  const projects = useQuery({queryKey: ['projects', mode], queryFn: listProjects, enabled: import.meta.env.DEV});
  if (!import.meta.env.DEV) return <Preparing title="TFT 프로젝트" />;
  const items = projects.data?.data.items;
  return <main>
    <header><p className="eyebrow">TFT</p><h1>프로젝트 목록</h1></header>
    <section>
      {projects.isPending && <Loading />}
      {projects.isError && <ErrorState error={projects.error} onRetry={() => projects.refetch()} />}
      {items && items.length === 0 && <Empty message="프로젝트가 없습니다." />}
      {items && items.length > 0 && <table><thead><tr><th>이름</th><th>상태</th><th>책임자</th><th>구성안</th><th>출처</th></tr></thead>
        <tbody>{items.map(item => <tr key={item.id}><td><Link to={item.id}>{item.name}</Link></td><td><StatusBadge code={item.status} labels={PROJECT_STATUS_LABEL} /></td><td>{item.responsible_user_id ? '지정됨' : '미지정'}</td><td>{item.current_composition_version ? `판본 ${item.current_composition_version}` : '없음'}</td><td><small>{item.data_origin}</small></td></tr>)}</tbody></table>}
    </section>
  </main>;
}
