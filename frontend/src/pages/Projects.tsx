import { useQuery } from '@tanstack/react-query';
import { getApiMode } from '../api/client';
import { listProjects } from '../api/tft';

export default function Projects() {
  const mode = getApiMode();
  const projects = useQuery({queryKey: ['projects', mode], queryFn: listProjects});
  return <main>
    <header><h1>TFT 프로젝트</h1></header>
    <section role="status">{projects.isPending && '불러오는 중'}{projects.isError && <span className="error">{projects.error.message}</span>}
      {projects.data?.data.items.map(item => <p key={item.id}>{item.name} <small>{item.data_origin}</small></p>)}
      {projects.data?.data.items.length === 0 && '프로젝트가 없습니다.'}</section>
  </main>;
}
