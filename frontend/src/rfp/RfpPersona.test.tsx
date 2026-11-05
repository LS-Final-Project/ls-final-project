import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { RfpPersona } from './RfpPersona';

it('엘로로 상태를 보이고 멈춤·완료면 움직이지 않는다', () => {
  const view = render(<RfpPersona state="thinking" paused={false} label="AI 분석 중" />);
  expect(screen.getByRole('img', { name: 'AI 분석 중' }).getAttribute('data-state')).toBe('thinking');
  expect(view.container.querySelector('.ello-work.still')).toBeNull();
  view.rerender(<RfpPersona state="idle" paused label="분석 완료" completed />);
  expect(view.container.querySelector('.ello-work.still')).not.toBeNull();
});
