import { useInfiniteQuery } from '@tanstack/react-query';
import { getApiMode } from '../api/client';
import { listTickets } from '../api/cs';

// 문의 목록 쪽수 조회. 업무 홈과 문의 조회가 같은 캐시를 쓰도록 키와 옵션을 한 곳에 둔다.
// 키는 ['tickets', mode]와 겹치면 안 된다. 개발 환경 화면(DevCheck)이 그 키에 쪽수 없는 일반 결과를 저장해 두기 때문에
// 같은 키를 무한 조회로 읽으면 pages가 없어 화면이 깨진다.
export function useTicketPages(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ['ticket-pages', getApiMode()],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => listTickets(pageParam, signal),
    getNextPageParam: last => last.data.next_cursor ?? undefined,
    enabled,
  });
}
