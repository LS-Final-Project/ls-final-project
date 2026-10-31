import TicketWorkflow from '../components/TicketWorkflow';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getApiMode } from '../api/client';
import { listTickets } from '../api/cs';
import { Empty, ErrorState, Loading } from '../components/states';

export default function Tickets() {
  const mode = getApiMode();
  const access = useBusinessAccess('cs');
  const [cursors,setCursors]=useState<(string|undefined)[]>([undefined]);
  const cursor=cursors.at(-1);
  const [product,setProduct]=useState('');
  const [search,setSearch]=useState('');const [channel,setChannel]=useState('');const [sort,setSort]=useState('newest');
  const tickets = useQuery({queryKey: ['tickets', mode, cursor], queryFn: ({signal})=>listTickets(cursor, signal), enabled: access.ready});
  if (!access.ready) return <BusinessGate access={access} title="C/S 문의" />;
  const items = tickets.isSuccess ? tickets.data.data.items.filter(item=>(!product||item.product_code===product)&&(!channel||item.channel===channel)&&`${item.subject??''} ${item.product_code} ${item.external_id??''}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())).slice().sort((a,b)=>sort==='subject'?(a.subject??'').localeCompare(b.subject??''):sort==='oldest'?Date.parse(a.received_at)-Date.parse(b.received_at):Date.parse(b.received_at)-Date.parse(a.received_at)) : undefined;
  return <main>
    <header><p className="eyebrow">C/S</p><h1>문의 목록</h1></header>
    <button disabled={tickets.isFetching} onClick={()=>tickets.refetch()}>최신 상태 조회</button>
    <section><h2>현재 페이지 필터</h2><p>검색·정렬은 현재 불러온 최대 50건에 적용됩니다. 전체 문의 검색은 아직 지원하지 않습니다.</p>
      <label>제목·제품·외부 ID 검색<input value={search} onChange={e=>setSearch(e.target.value)}/></label>
      <label>제품 코드<select value={product} onChange={e=>setProduct(e.target.value)}><option value="">전체</option>{Array.from(new Set([...(tickets.isSuccess?tickets.data.data.items.map(item=>item.product_code):[]),...(product?[product]:[])])).sort().map(code=><option key={code} value={code}>{code}</option>)}</select></label>
      <label>접수 채널<select value={channel} onChange={e=>setChannel(e.target.value)}><option value="">전체</option>{['EMAIL','PARTNER_PORTAL','WEB_FORM','PHONE','CHAT'].map(value=><option key={value}>{value}</option>)}</select></label>
      <label>현재 페이지 정렬<select value={sort} onChange={e=>setSort(e.target.value)}><option value="newest">최신 접수순</option><option value="oldest">오래된 접수순</option><option value="subject">제목순</option></select></label>
      <button onClick={()=>{setSearch('');setChannel('');setProduct('');setSort('newest');}}>필터 초기화</button>
      <p>페이지 {cursors.length} · {items?.length??0}건 표시</p>
      {tickets.isPending && <Loading />}
      {tickets.isError && <ErrorState error={tickets.error} onRetry={() => tickets.refetch()} />}
      {items && items.length === 0 && <Empty message={tickets.data?.data.items.length ? "현재 페이지에서 조건에 맞는 문의가 없습니다." : "문의가 없습니다."} />}
      {items && items.length > 0 && <table><thead><tr><th>제목</th><th>채널</th><th>제품</th><th>접수</th><th>출처</th><th>업무 상태·안내</th></tr></thead>
        <tbody>{items.map(item => <tr key={item.id}><td><Link to={item.id}>{item.subject || '(제목 없음)'}</Link></td><td>{item.channel}</td><td>{item.product_code}</td><td>{new Date(item.received_at).toLocaleString('ko-KR')}</td><td><small>{item.data_origin}</small></td><td><TicketWorkflow state={item.workflow_state}/></td></tr>)}</tbody></table>}
    <button disabled={tickets.isFetching||cursors.length===1} onClick={()=>setCursors(cursors.slice(0,-1))}>이전 페이지</button>
      <button disabled={tickets.isFetching||!tickets.isSuccess||!tickets.data.data.next_cursor} onClick={()=>{if(tickets.data?.data.next_cursor)setCursors([...cursors,tickets.data.data.next_cursor]);}}>다음 페이지</button>
      <p>업무 상태는 API 조회 결과입니다. 다음 작업 안내는 상태별 일반 권고이며 실행 권한이나 처리 완료를 뜻하지 않습니다. 문의 제목에서 원문을 확인하세요. AI 분류와 담당자 변경은 연결 준비 중입니다.</p>
    </section>
  </main>;
}
