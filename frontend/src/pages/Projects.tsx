import { useQuery } from '@tanstack/react-query';
import { getApiMode } from '../api/client';
import { listProjects } from '../api/tft';
import { Empty, ErrorState, Loading, Preparing } from '../components/states';

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
      {items && items.length > 0 && <table><thead><tr><th>이름</th><th>출처</th></tr></thead>
        <tbody>{items.map(item => <tr key={item.id}><td>{item.name}</td><td><small>{item.data_origin}</small></td></tr>)}</tbody></table>}
    </section>
  </main>;
}
