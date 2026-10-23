import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listTickets } from '../api/cs';
import { Empty, ErrorState, Loading, Preparing } from '../components/states';

export default function Tickets() {
  const mode = getApiMode();
  const tickets = useQuery({queryKey: ['tickets', mode], queryFn: listTickets, enabled: import.meta.env.DEV});
  if (!import.meta.env.DEV) return <Preparing title="C/S 문의" />;
  const items = tickets.data?.data.items;
  return <main>
    <header><p className="eyebrow">C/S</p><h1>문의 목록</h1></header>
    <section>
      {tickets.isPending && <Loading />}
      {tickets.isError && <ErrorState error={tickets.error} onRetry={() => tickets.refetch()} />}
      {items && items.length === 0 && <Empty message="문의가 없습니다." />}
      {items && items.length > 0 && <table><thead><tr><th>제목</th><th>채널</th><th>제품</th><th>접수</th><th>출처</th></tr></thead>
        <tbody>{items.map(item => <tr key={item.id}><td><Link to={item.id}>{item.subject || '(제목 없음)'}</Link></td><td>{item.channel}</td><td>{item.product_code}</td><td>{new Date(item.received_at).toLocaleString('ko-KR')}</td><td><small>{item.data_origin}</small></td></tr>)}</tbody></table>}
    </section>
  </main>;
}
