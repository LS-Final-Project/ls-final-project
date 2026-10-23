import { useQuery } from '@tanstack/react-query';
import { getApiMode } from '../api/client';
import { listTickets } from '../api/cs';

export default function Tickets() {
  const mode = getApiMode();
  const tickets = useQuery({queryKey: ['tickets', mode], queryFn: listTickets});
  return <main>
    <header><h1>C/S 문의</h1></header>
    <section role="status">{tickets.isPending && '불러오는 중'}{tickets.isError && <span className="error">{tickets.error.message}</span>}
      {tickets.data?.data.items.map(item => <p key={item.id}>{item.subject} <small>{item.data_origin}</small></p>)}
      {tickets.data?.data.items.length === 0 && '문의가 없습니다.'}</section>
  </main>;
}
