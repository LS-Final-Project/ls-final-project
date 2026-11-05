import { useEffect, useRef } from 'react';

// 작업 공간·초안 화면의 칸을 화면 높이에 맞춘다(페이지 전체 스크롤 없이 함께 보이게). 칸 위치는 위쪽 띠에 따라 달라져 재서 넣는다.
export function useFitToViewport() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const fit = () => el.style.setProperty('--ws-grid-top', `${Math.max(0, el.getBoundingClientRect().top + window.scrollY)}px`);
    fit();
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(fit); observer?.observe(document.body);
    window.addEventListener('resize', fit);
    return () => { observer?.disconnect(); window.removeEventListener('resize', fit); };
  }, []);
  return ref;
}
