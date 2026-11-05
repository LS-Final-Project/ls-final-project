import face from '../assets/ello-face.png';
import body from '../assets/ello.png';
import './ello.css';

/** AI가 만든 것 표시(엘로 얼굴). 엘로가 보이면 AI 초안, 사람이 확인할 대상이다. */
export function ElloMark({ size = 18 }: { size?: number }) {
  return <img className="ello-mark" src={face} alt="" aria-hidden="true" width={size} height={size} />;
}

/** AI 작업 중 표시(엘로가 위아래로 움직인다). 멈춤·실패면 still. */
export function ElloWorking({ size = 30, still = false }: { size?: number; still?: boolean }) {
  return <img className={`ello-work${still ? ' still' : ''}`} src={body} alt="" aria-hidden="true" width={size} height={Math.round(size * 1.06)} />;
}
