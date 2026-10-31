import { createContext, useContext } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';

export const Loading = ({ label = '불러오는 중' }: { label?: string }) => <p role="status" className="state">{label}</p>;

export const Empty = ({ message }: { message: string }) => <p className="state">{message}</p>;

// 새 업무 셸 안인지 알린다. 셸 안에서는 오류 화면 보드(CM0901)의 모양으로, 밖(옛 화면)에서는 이전 모양으로 그린다.
export const InShell = createContext(false);

// 401·403·404는 재시도해도 결과가 같으므로 안내만 하고, 그 밖의 오류는 다시 시도할 수 있게 한다.
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const inShell = useContext(InShell);
  const api = error instanceof ApiError ? error : null;
  const status = api?.status;
  const fixed = status === 401 || status === 403 || status === 404;
  // fetch가 응답 없이 실패하면(업무 서비스 중계가 꺼진 경우 등) TypeError가 온다.
  const unreachable = !api && error instanceof TypeError;
  const title = unreachable ? '업무 서버에 연결할 수 없습니다' : status === 401 ? '로그인이 필요합니다' : status === 403 ? '권한이 없습니다' : status === 404 ? '찾을 수 없습니다' : '요청을 처리하지 못했습니다';
  const hint = unreachable ? '웹 서버는 응답했지만 업무 서비스 중계가 꺼져 있거나 응답이 없습니다. 입력 중이던 내용은 이 화면에 남아 있습니다.'
    : status === 401 ? '세션이 없거나 만료됐습니다.' : status === 403 ? '허용된 역할과 범위를 확인하세요.' : status === 404 ? '없거나 접근할 수 없는 항목입니다.' : api?.message || (error instanceof Error ? error.message : '알 수 없는 오류');
  const loginHelp = <>
    {status === 401 && !import.meta.env.DEV && <p><Link to="/account">계정 확인</Link>에서 다시 로그인하세요.</p>}
    {status === 401 && import.meta.env.DEV && <p><Link to="/">개발 환경</Link>에서 시연 계정을 선택하세요.</p>}
  </>;
  if (!inShell) return <div role="alert" className="state error">
    <strong>{title}</strong><p>{hint}</p>
    {loginHelp}
    {api?.requestId && <small>요청 ID {api.requestId}</small>}
    {onRetry && !fixed && <button onClick={onRetry}>다시 시도</button>}
  </div>;
  const tag = unreachable ? '연결 안 됨' : status ? String(status) : '오류';
  const copy = (text: string) => { void navigator.clipboard?.writeText(text).catch(() => undefined); };
  return <div role="alert" className="kv-err">
    <h2>{title}<span className="auto" style={{ marginLeft: 10 }}>{tag}</span></h2>
    <p>{hint}</p>
    {loginHelp}
    <div className="kv-actions">
      {status === 403 && <Link className="kb primary" to="/cs">업무 홈으로</Link>}
      {onRetry && !fixed && <button className="kb primary" onClick={onRetry}>다시 시도</button>}
      {api?.requestId && status !== undefined && status >= 500 && <button className="kb" onClick={() => copy(api.requestId!)}>오류 번호 복사</button>}
    </div>
    {api?.requestId && <div className="meta">요청 ID {api.requestId}</div>}
  </div>;
}

// inline은 화면 안의 한 구역만 준비 중일 때(예: 아직 연결되지 않은 API) 쓴다. 기본은 화면 전체다.
export const Preparing = ({ title, message = '업무 화면은 준비 중입니다.', inline = false }: { title: string; message?: string; inline?: boolean }) =>
  inline ? <section><h2>{title}</h2><p className="state">{message}</p></section> : <main><header><h1>{title}</h1></header><section><p>{message}</p></section></main>;

export const NotFoundPage = () => <main><header><h1>페이지를 찾을 수 없습니다</h1></header><p>주소를 확인하거나 <Link to="/">처음 화면</Link>으로 이동하세요.</p></main>;
