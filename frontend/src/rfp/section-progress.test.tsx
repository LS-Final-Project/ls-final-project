import { describe, expect, it } from 'vitest';
import { sectionStates } from './RfpWorkspace';

const ev = (sequence: number, kind: 'section' | 'found' | 'compare', label: string, n?: number) =>
  ({ sequence, occurredAt: '2026-11-05T00:00:00Z', type: 'progress', step: 'extract', focus: { kind, label, n } });

describe('분석 중 조항 진행', () => {
  it('이름만 온 조항은 읽는 중, 건수가 온 조항은 다 읽음이고 처음 나온 순서를 지킨다', () => {
    const rows = sectionStates([
      ev(3, 'found', '제2조 공급 범위', 3),
      ev(1, 'section', '제2조 공급 범위'),
      ev(2, 'section', '제5조 정격'),
      ev(4, 'compare', '자사 제품', 9),
    ]);
    expect(rows).toEqual([{ label: '제2조 공급 범위', n: 3 }, { label: '제5조 정격', n: undefined }]);
  });
});
