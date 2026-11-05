import { useQuery } from '@tanstack/react-query';
import { getApiMode } from '../api/client';
import { listUsers } from '../api/rfp';

// 화면의 사용자 ID를 모아 한 번에 이름으로 바꾼다. 이름을 못 찾은 ID는 빈 문자열이다.
export function useNames(ids: (string | null | undefined)[]) {
  const unique = [...new Set(ids.filter((id): id is string => !!id))].sort().slice(0, 100);
  const names = useQuery({
    queryKey: ['user-names', getApiMode(), unique.join(',')],
    queryFn: () => listUsers(unique),
    enabled: unique.length > 0,
    staleTime: 300_000,
  });
  const found = new Map(names.data?.data.items?.map(user => [user.user_id, user.display_name]));
  return (id: string | null | undefined) => (id && found.get(id)) || '';
}
