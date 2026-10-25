import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';

export const Loading = ({ label = '불러오는 중' }: { label?: string }) => <p role="status" className="state">{label}</p>;

export const Empty = ({ message }: { message: string }) => <p className="state">{message}</p>;

// 401·403·404는 재시도해도 결과가 같으므로 안내만 하고, 그 밖의 오류는 다시 시도할 수 있게 한다.
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  const status = api?.status;
  const fixed = status === 401 || status === 403 || status === 404;
  const title = status === 401 ? '로그인이 필요합니다' : status === 403 ? '권한이 없습니다' : status === 404 ? '찾을 수 없습니다' : '요청을 처리하지 못했습니다';
  const hint = status === 401 ? '세션이 없거나 만료됐습니다.' : status === 403 ? '허용된 역할과 범위를 확인하세요.' : status === 404 ? '없거나 접근할 수 없는 항목입니다.' : api?.message || (error instanceof Error ? error.message : '알 수 없는 오류');
  return <div role="alert" className="state error">
    <strong>{title}</strong><p>{hint}</p>
    {status === 401 && import.meta.env.DEV && <p><Link to="/">개발 환경</Link>에서 시연 계정을 선택하세요.</p>}
    {api?.requestId && <small>요청 ID {api.requestId}</small>}
    {onRetry && !fixed && <button onClick={onRetry}>다시 시도</button>}
  </div>;
}

// inline은 화면 안의 한 구역만 준비 중일 때(예: 아직 연결되지 않은 API) 쓴다. 기본은 화면 전체다.
export const Preparing = ({ title, message = '업무 화면은 준비 중입니다.', inline = false }: { title: string; message?: string; inline?: boolean }) =>
  inline ? <section><h2>{title}</h2><p className="state">{message}</p></section> : <main><header><h1>{title}</h1></header><section><p>{message}</p></section></main>;

export const NotFoundPage = () => <main><header><h1>페이지를 찾을 수 없습니다</h1></header><p>주소를 확인하거나 <Link to="/">처음 화면</Link>으로 이동하세요.</p></main>;
