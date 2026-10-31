import type { ReactNode } from 'react';
import logo from '../ls-shell/ls-logo.svg';
import './login.css';

// 로그인 화면(보드 CS_K_Login). 세션이 없거나 만료됐을 때 계정 화면을 덮는다.
// 개발 모드에서는 운영 로그인을 실행하지 않고 시연 계정 안내를 보인다.
export default function LoginScreen({ operational, signedOut, notice }: { operational: boolean; signedOut: boolean; notice?: ReactNode }) {
  return <div className="ls-login">
    <header className="lg-top"><img src={logo} width="44" height="20" alt="LS" />업무지원{!operational && <small>개발 모드</small>}</header>
    <main className="lg-main"><div className="lg-wrap">
      <section className="lg-card" aria-labelledby="lg-h">
        <h1 id="lg-h">{signedOut ? '다시 로그인' : '로그인'}</h1>
        <div className="lg-body">
          {signedOut && <p role="status">로그아웃했습니다.</p>}
          {!operational && <p role="note" className="lg-strip">개발 모드입니다. 시연 계정은 Microsoft 로그인이나 사서함 연결을 확인한 계정이 아닙니다. 이 화면에서 운영 로그인·메일 연결을 실행할 수 없습니다.</p>}
          {notice}
          <p>세션이 없거나 만료되었습니다. Microsoft 계정으로 다시 로그인하세요.</p>
          {operational
            ? <a className="lg-btn" href="/api/v1/platform/auth/login"><MicrosoftMark />Microsoft로 로그인</a>
            : <span className="lg-btn" role="link" aria-disabled="true"><MicrosoftMark />Microsoft로 로그인</span>}
          <p style={{ margin: '12px 0 0', color: 'var(--muted)' }}>서비스 로그인과 메일 연결은 별도입니다.</p>
        </div>
      </section>
    </div></main>
    <footer className="lg-bottom"><span className="l">LS ELECTRIC</span><span>업무지원</span></footer>
  </div>;
}

const MicrosoftMark = () => <svg className="ms" viewBox="0 0 21 21" aria-hidden="true">
  <rect x="1" y="1" width="9" height="9" fill="#f25022" /><rect x="11" y="1" width="9" height="9" fill="#7fba00" />
  <rect x="1" y="11" width="9" height="9" fill="#00a4ef" /><rect x="11" y="11" width="9" height="9" fill="#ffb900" />
</svg>;
