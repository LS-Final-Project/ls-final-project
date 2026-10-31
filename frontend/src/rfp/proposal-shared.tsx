import { useRef } from 'react';
import { ApiError } from '../api/client';
import { ErrorState } from '../components/states';

// 같은 시도의 재전송은 같은 키로 보내 서버가 두 번 처리하지 않게 한다. 보낼 내용이 바뀌면 다른 시도이므로 새 키다.
// 거절(4xx)이나 성공 뒤에는 next로 새 키를 받는다.
export function useAttemptKey(payload: string) {
  const attempt = useRef({ payload, key: crypto.randomUUID() });
  if (attempt.current.payload !== payload) attempt.current = { payload, key: crypto.randomUUID() };
  return [attempt.current.key, () => { attempt.current.key = crypto.randomUUID(); }] as const;
}
export const settled = (error: unknown) => error instanceof ApiError && error.status < 500;

// 재시도해도 소용없는 충돌은 다음 행동을 알려 준다. 입력한 내용은 지우지 않는다.
const CONFLICT: Record<string, string> = {
  TFT_VERSION_CONFLICT: '다른 사람이 먼저 바꿨습니다. 요구가 그사이 바뀌어 승인하지 않았습니다. 최신 내용을 보고 다시 제안하세요. 기각은 할 수 있습니다.',
  TFT_PROPOSAL_DECIDED: '이미 처리된 제안입니다. 다시 불러와 확인하세요.',
};
export function Failure({ error, onReload, messages = CONFLICT }: { error: unknown; onReload: () => void; messages?: Record<string, string> }) {
  const code = error instanceof ApiError ? error.code : undefined;
  if (code && messages[code]) return <div role="alert" className="kv-next" style={{ margin: '6px 0 0' }}>
    <span>{messages[code]}</span>
    <button type="button" className="kb" onClick={onReload}>다시 불러오기</button>
  </div>;
  return <ErrorState error={error} />;
}
