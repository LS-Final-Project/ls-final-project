import type { components } from './generated/tft';
import { request, sessionCsrfToken } from './client';

// 제품 목록(KAN-246). 저장은 PUT + expected_version이라 Idempotency-Key가 없다(같은 요청을 다시 보내면 409).
type Schemas = components['schemas'];
export type CatalogOptions = Schemas['Options'];
export type OwnProduct = Schemas['OwnProduct'];
export type OwnProductInput = Schemas['OwnProductInput'];
export type Partner = Schemas['Partner'];
export type PartnerProfile = Partner['profile'];
export type PartnerInput = Schemas['PartnerInput'];
export type SpecFact = OwnProduct['spec_facts'][number];
export type SpecCondition = NonNullable<SpecFact['conditions']>[number];

type Envelope<T> = { data: T };
const put = (body: unknown): RequestInit => {
  const csrf = sessionCsrfToken();
  return { method: 'PUT', headers: csrf ? { 'X-CSRF-Token': csrf } : {}, body: JSON.stringify(body) };
};

export const catalogOptions = () => request<Envelope<CatalogOptions>>('/api/v1/tft/catalog/options');
export const listOwnProducts = () => request<Envelope<OwnProduct[]>>('/api/v1/tft/catalog/own-products');
export const saveOwnProduct = (code: string, body: OwnProductInput) =>
  request<Envelope<OwnProduct>>(`/api/v1/tft/catalog/own-products/${encodeURIComponent(code)}`, put(body));
export const listPartners = () => request<Envelope<Partner[]>>('/api/v1/tft/catalog/partners');
export const savePartner = (code: string, body: PartnerInput) =>
  request<Envelope<Partner>>(`/api/v1/tft/catalog/partners/${encodeURIComponent(code)}`, put(body));

// 사양 조건을 한 줄 글로: "voltage=415 V; current_type=AC". 읽을 때는 숫자 값 뒤의 짧은 마지막 낱말만 단위로 본다
// ("insulation=Class F"는 값 'Class F'). 화면은 조건 칸을 고친 줄만 이 글을 다시 읽는다.
export const conditionsText = (conditions: SpecCondition[] | null | undefined) =>
  (conditions ?? []).map(c => `${c.name}=${c.value}${c.unit ? ` ${c.unit}` : ''}`).join('; ');
export function parseConditions(text: string): SpecCondition[] | null {
  const parts = text.split(';').map(s => s.trim()).filter(Boolean);
  if (!parts.length) return null;
  return parts.map(part => {
    const [name, ...rest] = part.split('=');
    const words = rest.join('=').trim().split(/\s+/);
    const last = words.at(-1) ?? '';
    const unit = words.length > 1 && /\d/.test(words.slice(0, -1).join(' ')) && /^[A-Za-z°µΩ%/·]{1,6}\d?$/.test(last) ? words.pop() : undefined;
    return { name: name.trim(), value: words.join(' '), ...(unit ? { unit } : {}) };
  });
}
// 협력사 코드는 'A-B' 형식이다. 시드는 P-로 시작하니 새 등록은 R-A부터 빈 코드를 고른다.
export function nextPartnerCode(taken: string[]) {
  const used = new Set(taken);
  for (const first of 'RSTUVWXYZABCDEFGHIJKLMNOPQ') for (const second of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') {
    const code = `${first}-${second}`;
    if (!used.has(code)) return code;
  }
  return '';
}
