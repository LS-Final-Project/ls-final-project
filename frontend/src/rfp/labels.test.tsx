import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { ATTRIBUTE_LABEL, partnerNote, reasonText, requirementNo } from './labels';

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf-8');

describe('공용 표시 값', () => {
  it('요구 번호에서 원시 값을 지운다', () => {
    expect(requirementNo('738dbba4:1.1.A.1:1')).toBe('1.1.A.1-1');
    expect(requirementNo('R-01')).toBe('R-01');
    expect(requirementNo('738dbba4:OCR-page-1-region-3:2')).toBe('이미지 요구 2');
    expect(requirementNo('44444444-4444-4444-8444-444444444441')).toBe('요구 번호 미기재');
    expect(requirementNo('')).toBe('요구 번호 미기재');
    expect(requirementNo('doc:2.1:1')).toBe('2.1-1');
    expect(requirementNo('doc:44444444-4444-4444-8444-444444444441:1')).toBe('요구 번호 미기재');
  });

  it('속성 라벨은 서버 어휘 전부를 덮는다(tests/tft/test_rfp_requirements.py와 같은 목록)', () => {
    expect(Object.keys(ATTRIBUTE_LABEL).sort()).toEqual(JSON.parse(read('../../../docs/openapi/tft-attribute-keys.json')));
  });
});

describe('reasonText', () => {
  it('근거 ID·조항 코드·영문 키를 빼고 판정 말을 문장으로 바꾼다', () => {
    expect(reasonText('이미지 전사 원문 확인 필요. 원문 OCR-DOCX-0001.7 정격전압 요구 11 kV, 자사 25.8 kV(근거 DS-VCB-25kV-1250 2.1): 값 다름.'))
      .toBe('요구 11 kV, 자사 25.8 kV. 값이 다릅니다.');
    expect(reasonText('원문 2.5 secondary_voltage 요구 480 V, 자사 415 V(근거 DS-TRF-DRY-630 2.1): 미달.'))
      .toBe('요구 480 V, 자사 415 V. 요구에 못 미칩니다.');
    expect(reasonText('자사 사양 자료에 이 항목이 없어 비교하지 않았습니다.')).toBe('자사 사양 자료에 이 항목이 없어 비교하지 않았습니다.');
    expect(reasonText('원문 제6조.6 개폐반: 요구값 미정(자리표시자). 충족 판정에서 제외.')).toBe('원문에 값이 비어 있습니다. 고객 질의가 필요합니다.');
    expect(reasonText('원문 제2조.1 임피던스 요구 6 %, 자사 6 %(근거 DS-TRF-DRY-630 2.2): 값은 충족하나 조건이 달라 확인 필요. 조건 차이: reference temperature 요구 75 °C, 후보 120 °C.'))
      .toBe('요구 6 %, 자사 6 %: 값은 맞지만 조건이 다릅니다. 기준온도 차이: 요구 75 °C, 자사 120 °C.');
    expect(reasonText('검색 범위(family=LV_SWITCHGEAR, voltage_class=LV, 문서 0건)에서 자사 후보를 찾지 못함. 회사 전체 미보유나 참여 불가를 뜻하지 않음.'))
      .toBe('자사 제품 자료에서 맞는 후보를 찾지 못했습니다.');
    expect(reasonText('원문 제10조.1 공장시험: 수행 과업 요구. 수행 주체 확인(자사/협력사) 필요.')).toBe('수행 과업입니다. 자사가 할지 협력사가 할지 정하세요.');
  });
});

describe('partnerNote', () => {
  it('제외 사유와 통합 책임을 한 줄로 줄인다', () => {
    expect(partnerNote({ exclusion_reasons: ['원문 제2조.1 단시간 내전류 요구 25 kA 이상, 후보 20 kA(근거 SYN-DS-SB-01 2.2): 미달.'],
      reasons: ['조립품 공급이라 통합 책임(조립)이 필요: 후보 가능.', '조립품 공급이라 통합 책임(연동)이 필요: 후보 미확인.', '조립품 공급이라 통합 책임(보증)이 필요: 후보 미확인.'] }).text)
      .toBe('단시간 내전류 20 kA, 요구 25 kA 이상 미달 / 통합 책임 미확인: 연동·보증');
    expect(partnerNote({ exclusion_reasons: ['조립품 공급이라 통합 책임(조립)이 필요: 후보 불가.', '조립품 공급이라 통합 책임(시험)이 필요: 후보 불가.'],
      reasons: ['원문 제2조.1 단시간 내전류 요구 25 kA 이상: 제안 모델에 같은 속성 근거가 없어 확인 필요.'] }).text)
      .toBe('통합 책임 불가: 조립·시험 / 단시간 내전류 자료 없음, 확인 필요');
  });
});
