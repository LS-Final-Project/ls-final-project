import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState, type ReactNode } from 'react';
import { getApiMode } from '../api/client';
import {
  catalogOptions, conditionsText, listOwnProducts, listPartners, nextPartnerCode, parseConditions, saveOwnProduct, savePartner,
  type CatalogOptions, type OwnProduct, type OwnProductInput, type Partner, type PartnerInput, type SpecCondition, type SpecFact,
} from '../api/catalog';
import { BusinessGate, useBusinessAccess } from '../components/BusinessAccess';
import { ErrorState, Loading } from '../components/states';
import { KvBox, KvCond, KvCondField, KvCondRow, KvFoot, KvFormCell, KvFormRow, KvGrid, KvModal, KvNext, KvRibbon, KvTitle, type KvColumn } from '../ls-shell/kv';
import { ATTRIBUTE_LABEL, CERT_KIND_LABEL, CONFIRMATION_LABEL, FAMILY_LABEL, LIFECYCLE_LABEL, SCOPE_LABEL, label, shortDateTime } from './labels';
import { useNames } from './names';
import { Failure } from './proposal-shared';
import './RfpWorkspace.css';
import './GroupActions.css';
import './RfpCatalog.css';

type Tab = 'own' | 'partner';
const CODE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const MESSAGES = {
  TFT_VERSION_CONFLICT: '다른 사람이 먼저 고쳤습니다. 목록을 다시 불러온 뒤 고쳐 주세요.',
  TFT_PRODUCT_CODE_TAKEN: '다른 협력사가 이미 쓰는 제품 코드입니다. 제품 코드를 바꿔 주세요.',
};
const attributeName = (key: string) => ATTRIBUTE_LABEL[key] ?? key;
const list = (text: string) => text.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
const lines = (text: string) => text.split('\n').map(s => s.trim()).filter(Boolean);
const capable = (value: boolean | null | undefined) => value == null ? '미확인' : value ? '가능' : '불가';

// 자사 제품·협력사 목록과 등록·수정(KAN-246). 비교와 협력사 채점이 이 목록을 쓴다.
export default function RfpCatalog() {
  const mode = getApiMode();
  const access = useBusinessAccess('tft');
  const [tab, setTab] = useState<Tab>('own');
  const [text, setText] = useState('');
  const [family, setFamily] = useState('');
  const [query, setQuery] = useState({ text: '', family: '' });
  const [picked, setPicked] = useState<string>();
  const [editing, setEditing] = useState<{ kind: Tab; code?: string }>();
  const options = useQuery({ queryKey: ['catalog-options', mode], queryFn: catalogOptions, enabled: access.ready, staleTime: 300_000 });
  const own = useQuery({ queryKey: ['catalog-own', mode], queryFn: listOwnProducts, enabled: access.ready });
  const partners = useQuery({ queryKey: ['catalog-partners', mode], queryFn: listPartners, enabled: access.ready });
  const ownRows = own.data?.data ?? [];
  const partnerRows = partners.data?.data ?? [];
  const editors = useNames([...ownRows.map(p => p.edited_by), ...partnerRows.map(p => p.edited_by)]);
  if (!access.ready) return <BusinessGate access={access} title="제품 목록" />;

  const needle = query.text.toLowerCase();
  const ownShown = ownRows.filter(p => (!query.family || p.family === query.family)
    && (!needle || [p.product_code, ...p.standards].some(s => s.toLowerCase().includes(needle))));
  const partnerShown = partnerRows.filter(({ profile: p }) => (!query.family || p.product_family === query.family)
    && (!needle || [p.candidate_code, p.display_name, p.manufacturer_name, ...p.products.flatMap(x => [x.product_code, x.model])].some(s => s.toLowerCase().includes(needle))));
  const switchTab = (next: Tab) => { setTab(next); setPicked(undefined); setFamily(''); setQuery({ ...query, family: '' }); };
  const edited = (at: string | null | undefined, by: string | null | undefined) => at ? `${shortDateTime(at)}${editors(by) ? ` · ${editors(by)}` : ''}` : '─';

  const ownColumns: KvColumn<OwnProduct>[] = [
    { key: 'no', header: 'No', width: 40, className: 'n', cell: (_p, i) => i + 1 },
    { key: 'code', header: '제품 코드', className: 't', cell: p => <button type="button" className="rfp-catalog-link" onClick={() => setPicked(p.product_code)}>{p.product_code}</button> },
    { key: 'family', header: '계열', width: 110, cell: p => label(FAMILY_LABEL, p.family) },
    { key: 'life', header: '상태', width: 110, cell: p => <span className={p.lifecycle_status === 'DISCONTINUED' ? 'unc' : undefined}>{label(LIFECYCLE_LABEL, p.lifecycle_status)}</span> },
    { key: 'std', header: '적용 규격', width: 170, cell: p => p.standards.join(', ') || '─' },
    { key: 'cert', header: '인증', width: 150, cell: p => p.certifications.map(c => c.standard).join(', ') || '─' },
    { key: 'facts', header: '사양', width: 56, className: 'n', cell: p => p.spec_facts.length },
    { key: 'source', header: '자료', width: 100, className: 'c', cell: p => p.source === 'FILE' ? '기본 자료' : `수정 ${p.version}판` },
    { key: 'edited', header: '최근 수정', width: 170, cell: p => edited(p.edited_at, p.edited_by) },
  ];
  const partnerColumns: KvColumn<Partner>[] = [
    { key: 'code', header: '코드', width: 60, className: 'c', cell: ({ profile: p }) => p.candidate_code },
    { key: 'name', header: '협력사', className: 't', cell: ({ profile: p }) => <button type="button" className="rfp-catalog-link" onClick={() => setPicked(p.candidate_code)}>{p.display_name}</button> },
    { key: 'maker', header: '제조사', width: 150, cell: ({ profile: p }) => p.manufacturer_name },
    { key: 'family', header: '제품 계열', width: 170, cell: ({ profile: p }) => p.product_family },
    { key: 'models', header: '모델', width: 170, cell: ({ profile: p }) => p.products.map(x => x.model).join(', ') },
    { key: 'scope', header: '공급 범위', width: 120, cell: ({ profile: p }) => [...new Set(p.products.flatMap(x => x.scope))].map(s => label(SCOPE_LABEL, s)).join('·') },
    { key: 'confirm', header: '업체 확인', width: 80, className: 'c', cell: ({ profile: p }) => label(CONFIRMATION_LABEL, p.confirmation.status) },
    { key: 'source', header: '자료', width: 100, className: 'c', cell: ({ profile: p }) => `${p.profile_source === 'registered' ? '등록' : '기본'} ${p.profile_version}판` },
    { key: 'edited', header: '최근 수정', width: 170, cell: p => edited(p.edited_at, p.edited_by) },
  ];
  const ownPicked = tab === 'own' ? ownRows.find(p => p.product_code === picked) : undefined;
  const partnerPicked = tab === 'partner' ? partnerRows.find(p => p.profile.candidate_code === picked) : undefined;
  const current = tab === 'own' ? own : partners;
  const families = tab === 'own' ? options.data?.data.own_families ?? [] : options.data?.data.partner_families ?? [];
  const opts = options.data?.data;
  // 등록·수정은 관리자만 한다(KAN-247, 서버도 403). 다른 역할에는 단추를 보이지 않는다.
  const writer = access.user.data?.data.roles.includes('manager') === true;

  return <main className="rfp-ui rfp-catalog">
    <KvTitle path="제안 지원" title="제품 목록" code="TF0201">
      {writer && <button type="button" className="kb primary" disabled={!opts} onClick={() => setEditing({ kind: tab })}>{tab === 'own' ? '자사 제품 등록' : '협력사 등록'}</button>}
    </KvTitle>
    <KvNext label="안내">자사 비교와 협력사 채점이 이 목록을 씁니다. 자사 제품을 고치면 저장된 비교 판정은 그대로이고 다음 분석·다시 비교부터 반영됩니다. 협력사를 고치면 그 협력사의 기존 등급은 지난 판본 기준이라 후보 화면에서 빠지고, 다음 분석·다시 채점 때 새 판본으로 다시 매깁니다.{writer ? '' : ' 등록·수정은 관리자만 할 수 있습니다.'}</KvNext>
    <div className="ws-tabs rfp-catalog-tabs" role="tablist" aria-label="제품 목록">
      <button role="tab" aria-selected={tab === 'own'} className={tab === 'own' ? 'on' : ''} onClick={() => switchTab('own')}>자사 제품 <span className="muted">{ownRows.length}</span></button>
      <button role="tab" aria-selected={tab === 'partner'} className={tab === 'partner' ? 'on' : ''} onClick={() => switchTab('partner')}>협력사 <span className="muted">{partnerRows.length}</span></button>
    </div>
    <KvCond onSearch={() => setQuery({ text: text.trim(), family })}>
      <KvCondRow columns={3}>
        <KvCondField label="검색어"><input aria-label="검색어" value={text} maxLength={100} placeholder={tab === 'own' ? '제품 코드 또는 규격' : '협력사·제조사·모델'} onChange={e => setText(e.target.value)} /></KvCondField>
        <KvCondField label="계열"><select aria-label="계열" value={family} onChange={e => setFamily(e.target.value)}>
          <option value="">전체</option>
          {families.map(f => <option key={f} value={f}>{tab === 'own' ? label(FAMILY_LABEL, f) : f}</option>)}
        </select></KvCondField>
        <label><button type="submit" className="kb">조회</button></label>
      </KvCondRow>
    </KvCond>
    {current.isError ? <ErrorState error={current.error} onRetry={() => current.refetch()} />
      : tab === 'own'
        ? <KvGrid label="자사 제품" columns={ownColumns} rows={ownShown} rowKey={p => p.product_code} selectedKey={picked} onRow={p => setPicked(p.product_code)} scroll fallback={own.isPending ? <Loading /> : '조건에 맞는 제품이 없습니다.'} />
        : <KvGrid label="협력사" columns={partnerColumns} rows={partnerShown} rowKey={p => p.profile.candidate_code} selectedKey={picked} onRow={p => setPicked(p.profile.candidate_code)} scroll fallback={partners.isPending ? <Loading /> : '조건에 맞는 협력사가 없습니다.'} />}
    <KvFoot count={`${tab === 'own' ? ownShown.length : partnerShown.length}건`} />
    {ownPicked && <OwnDetail product={ownPicked} onEdit={writer ? () => setEditing({ kind: 'own', code: ownPicked.product_code }) : undefined} canEdit={!!opts} />}
    {partnerPicked && <PartnerDetail partner={partnerPicked} onEdit={writer ? () => setEditing({ kind: 'partner', code: partnerPicked.profile.candidate_code }) : undefined} canEdit={!!opts} />}
    {editing && opts && (editing.kind === 'own'
      ? <OwnForm options={opts} product={ownRows.find(p => p.product_code === editing.code)} taken={ownRows.map(p => p.product_code)} onClose={() => setEditing(undefined)} onSaved={code => { setTab('own'); setPicked(code); }} />
      : <PartnerForm options={opts} partner={partnerRows.find(p => p.profile.candidate_code === editing.code)} taken={partnerRows.map(p => p.profile.candidate_code)} onClose={() => setEditing(undefined)} onSaved={code => { setTab('partner'); setPicked(code); }} />)}
  </main>;
}

function FactTable({ facts, withProduct = false }: { facts: SpecFact[]; withProduct?: boolean }) {
  const columns: KvColumn<SpecFact & { i: number }>[] = [
    ...(withProduct ? [{ key: 'product', header: '제품 코드', width: 130, cell: (f: SpecFact) => f.product_code }] : []),
    { key: 'attr', header: '사양', width: 150, cell: f => attributeName(f.attribute) },
    { key: 'value', header: '값', className: 't', cell: f => `${f.value}${f.unit ? ` ${f.unit}` : ''}` },
    { key: 'cond', header: '조건', width: 200, cell: f => conditionsText(f.conditions) || '─' },
    { key: 'evidence', header: '근거', width: 170, cell: f => `${f.evidence_id}${f.section ? ` §${f.section}` : ''}` },
  ];
  return <KvGrid label="사양" columns={columns} rows={facts.map((f, i) => ({ ...f, i }))} rowKey={f => String(f.i)} fallback="등록된 사양이 없습니다. 사양이 없으면 비교에서 '자료 없음'이 됩니다." />;
}

function OwnDetail({ product, onEdit, canEdit }: { product: OwnProduct; onEdit?: () => void; canEdit: boolean }) {
  return <KvBox title={`${product.product_code} 상세`} note={product.source === 'FILE' ? '기본 자료(제품 마스터)' : `화면 수정 ${product.version}판`}>
    {onEdit && <div className="rfp-catalog-detail-head"><button type="button" className="kb" disabled={!canEdit} onClick={onEdit}>수정</button></div>}
    <KvRibbon cells={[['계열', label(FAMILY_LABEL, product.family)], ['상태', label(LIFECYCLE_LABEL, product.lifecycle_status)], ['적용 규격', product.standards.join(', ') || '─'], ['인증', product.certifications.map(c => `${c.standard}(${label(CERT_KIND_LABEL, c.kind)}${c.variant ? `, ${c.variant}` : ''})`).join(', ') || '─']]} />
    <FactTable facts={product.spec_facts} />
  </KvBox>;
}

function PartnerDetail({ partner, onEdit, canEdit }: { partner: Partner; onEdit?: () => void; canEdit: boolean }) {
  const p = partner.profile;
  const reviewer = useNames([p.confirmation.reviewer])(p.confirmation.reviewer) || p.confirmation.reviewer;
  return <KvBox title={`${p.candidate_code} ${p.display_name} 상세`} note={`${p.profile_source === 'registered' ? '화면 등록' : '기본 자료(합성)'} ${p.profile_version}판`}>
    {onEdit && <div className="rfp-catalog-detail-head"><button type="button" className="kb" disabled={!canEdit} onClick={onEdit}>수정</button></div>}
    <KvRibbon cells={[
      ['제조사', p.manufacturer_name], ['제품 계열', p.product_family], ['공급 지역', p.supply_regions.join(', ')],
      ['납기', p.lead_time ? `${p.lead_time.value}주${p.lead_time.ex_works ? '(공장 출하)' : ''}` : '─'],
      ['조립', capable(p.integrator_capable.assembly)], ['시험', capable(p.integrator_capable.test)], ['통합', capable(p.integrator_capable.integration)], ['통합 보증', capable(p.integrator_capable.warranty)],
      ['보증', p.warranty ?? '─'], ['업체 확인', `${label(CONFIRMATION_LABEL, p.confirmation.status)} · ${p.confirmation.as_of} · ${reviewer}`], ['유사 실적', p.similar_tasks.join(', ') || '─'], ['확인할 질문', p.confirmation.follow_up_questions.join(', ') || '─'],
    ]} />
    <KvGrid label="제품" columns={[
      { key: 'code', header: '제품 코드', width: 160, cell: x => x.product_code },
      { key: 'model', header: '모델', className: 't', cell: x => x.model },
      { key: 'scope', header: '범위', width: 220, cell: x => x.scope.map(s => label(SCOPE_LABEL, s)).join('·') },
    ]} rows={p.products} rowKey={x => x.product_code} fallback="제품이 없습니다." />
    <FactTable facts={p.spec_facts} withProduct />
    {p.certifications.length > 0 && <KvGrid label="인증" columns={[
      { key: 'std', header: '규격', width: 160, cell: c => c.standard },
      { key: 'kind', header: '종류', width: 110, cell: c => label(CERT_KIND_LABEL, c.kind) },
      { key: 'models', header: '적용 모델', className: 't', cell: c => c.applicable_models.join(', ') },
      { key: 'date', header: '확인일 · 유효기한', width: 190, cell: c => `${c.as_of} · ${c.valid_until ?? '기한 없음'}` },
      { key: 'evidence', header: '근거', width: 150, cell: c => c.evidence_id },
    ]} rows={p.certifications.map((c, i) => ({ ...c, i }))} rowKey={c => String(c.i)} fallback="" />}
    {p.notes && <p className="rfp-catalog-note">{p.notes}</p>}
  </KvBox>;
}

// --- 입력 ---------------------------------------------------------------------------------------
// original·shown: 불러온 조건과 그 글. 조건 칸을 고치지 않은 줄은 원래 조건 객체를 그대로 보낸다(글 왕복이 값을 바꾸지 않게).
type FactRow = { product_code: string; attribute: string; value: string; unit: string; conditions: string; evidence_id: string; section: string; original?: SpecCondition[] | null; shown?: string };
const factRow = (f?: SpecFact): FactRow => ({
  product_code: f?.product_code ?? '', attribute: f?.attribute ?? '', value: f?.value ?? '', unit: f?.unit ?? '',
  conditions: conditionsText(f?.conditions), evidence_id: f?.evidence_id ?? '', section: f?.section ?? '',
  original: f?.conditions, shown: conditionsText(f?.conditions),
});
const factBody = (f: FactRow) => ({
  attribute: f.attribute as SpecFact['attribute'], value: f.value.trim(), unit: f.unit.trim() || null,
  conditions: f.shown !== undefined && f.conditions === f.shown ? f.original ?? null : parseConditions(f.conditions), evidence_id: f.evidence_id.trim() || '제품 목록 등록', section: f.section.trim(),
});
const factProblem = (rows: FactRow[], withProduct: boolean) => rows.some(f => !f.attribute || !f.value.trim() || (withProduct && !f.product_code))
  ? `사양 줄마다 ${withProduct ? '제품 코드, ' : ''}사양, 값을 넣으세요. 쓰지 않는 줄은 지우세요.` : '';

function update<T>(rows: T[], index: number, patch: Partial<T>) { return rows.map((row, i) => i === index ? { ...row, ...patch } : row); }

function FactRows({ rows, onChange, options, products }: { rows: FactRow[]; onChange: (rows: FactRow[]) => void; options: CatalogOptions; products?: string[] }) {
  return <fieldset className="rfp-catalog-rows">
    <legend>사양 <small>속성은 비교 어휘에서 고릅니다. 조건은 "voltage=415 V; current_type=AC"처럼 적습니다.</small></legend>
    <table className="kv-grid free"><thead><tr>
      {products && <th style={{ width: 130 }}>제품 코드</th>}<th style={{ width: 160 }}>사양</th><th>값</th><th style={{ width: 70 }}>단위</th><th style={{ width: 190 }}>조건</th><th style={{ width: 130 }}>근거</th><th style={{ width: 54 }}>절</th><th style={{ width: 46 }} />
    </tr></thead><tbody>
      {rows.map((f, i) => <tr key={i}>
        {products && <td><select aria-label={`사양 ${i + 1} 제품 코드`} value={f.product_code} onChange={e => onChange(update(rows, i, { product_code: e.target.value }))}>
          <option value="">선택</option>{products.filter(Boolean).map(code => <option key={code} value={code}>{code}</option>)}
        </select></td>}
        <td><select aria-label={`사양 ${i + 1} 속성`} value={f.attribute} onChange={e => onChange(update(rows, i, { attribute: e.target.value }))}>
          <option value="">선택</option>{options.attributes.map(key => <option key={key} value={key}>{attributeName(key)}</option>)}
        </select></td>
        <td><input aria-label={`사양 ${i + 1} 값`} maxLength={200} value={f.value} onChange={e => onChange(update(rows, i, { value: e.target.value }))} /></td>
        <td><input aria-label={`사양 ${i + 1} 단위`} maxLength={32} value={f.unit} onChange={e => onChange(update(rows, i, { unit: e.target.value }))} /></td>
        <td><input aria-label={`사양 ${i + 1} 조건`} value={f.conditions} onChange={e => onChange(update(rows, i, { conditions: e.target.value }))} /></td>
        <td><input aria-label={`사양 ${i + 1} 근거`} maxLength={200} placeholder="제품 목록 등록" value={f.evidence_id} onChange={e => onChange(update(rows, i, { evidence_id: e.target.value }))} /></td>
        <td><input aria-label={`사양 ${i + 1} 절`} maxLength={64} value={f.section} onChange={e => onChange(update(rows, i, { section: e.target.value }))} /></td>
        <td className="c"><button type="button" className="kb sm" onClick={() => onChange(rows.filter((_r, j) => j !== i))}>삭제</button></td>
      </tr>)}
    </tbody></table>
    <button type="button" className="kb" onClick={() => onChange([...rows, factRow(products?.length === 1 ? { product_code: products[0] } as SpecFact : undefined)])}>사양 줄 추가</button>
  </fieldset>;
}

function SaveBar({ problem, pending, error, onClose, onReload, saveLabel }: { problem: string; pending: boolean; error: unknown; onClose: () => void; onReload: () => void; saveLabel: string }) {
  return <>
    {problem && <p role="alert" className="rfp-catalog-problem">{problem}</p>}
    {error !== null && error !== undefined && <Failure error={error} onReload={onReload} messages={MESSAGES} />}
    <div className="rfp-dialog-actions"><button type="button" className="kb" onClick={onClose}>취소</button><button type="submit" className="kb primary" disabled={!!problem || pending}>{pending ? '저장 중' : saveLabel}</button></div>
  </>;
}

function useSave<T>(save: () => Promise<T>, keys: string[], done: () => void) {
  const client = useQueryClient();
  const mode = getApiMode();
  const mutation = useMutation({ mutationFn: save, onSuccess: () => { for (const key of keys) client.invalidateQueries({ queryKey: [key, mode] }); done(); } });
  const reload = () => { for (const key of keys) client.invalidateQueries({ queryKey: [key, mode] }); mutation.reset(); done(); };
  return { mutation, reload };
}

function Field({ label: name, children }: { label: string; children: ReactNode }) { return <KvFormCell label={name}>{children}</KvFormCell>; }

function OwnForm({ options, product, taken, onClose, onSaved }: { options: CatalogOptions; product?: OwnProduct; taken: string[]; onClose: () => void; onSaved: (code: string) => void }) {
  const [code, setCode] = useState(product?.product_code ?? '');
  const [family, setFamily] = useState(product?.family ?? options.own_families[0] ?? '');
  const [life, setLife] = useState(product?.lifecycle_status ?? 'ACTIVE');
  const [standards, setStandards] = useState(product?.standards.join(', ') ?? '');
  const [certs, setCerts] = useState(product?.certifications.map(c => ({ standard: c.standard, kind: c.kind, evidence_id: c.evidence_id, variant: c.variant ?? '' })) ?? []);
  const [facts, setFacts] = useState<FactRow[]>(product?.spec_facts.map(factRow) ?? [factRow()]);
  const trimmed = code.trim();
  const body: OwnProductInput = {
    expected_version: product?.version ?? 0, family: family as OwnProductInput['family'], lifecycle_status: life as OwnProductInput['lifecycle_status'],
    standards: list(standards), certifications: certs.map(c => ({ standard: c.standard.trim(), kind: c.kind as 'MARK', evidence_id: c.evidence_id.trim() || '제품 목록 등록', variant: c.variant.trim() || null })),
    spec_facts: facts.map(factBody),
  };
  const problem = !product && !CODE.test(trimmed) ? '제품 코드는 영문·숫자로 시작하고 영문·숫자·.-_만 씁니다(64자 이내).'
    : !product && taken.includes(trimmed) ? '이미 있는 제품 코드입니다. 목록에서 그 제품을 골라 수정하세요.'
      : certs.some(c => !c.standard.trim()) ? '인증 줄마다 규격을 넣으세요.' : factProblem(facts, false);
  const { mutation, reload } = useSave(() => saveOwnProduct(product?.product_code ?? trimmed, body), ['catalog-own'], () => { onSaved(product?.product_code ?? trimmed); onClose(); });
  return <KvModal title={product ? `자사 제품 수정 · ${product.product_code}` : '자사 제품 등록'} onClose={onClose} className="rfp-dialog rfp-catalog-dialog" keepOnBackdrop>
    <form onSubmit={e => { e.preventDefault(); if (!problem) mutation.mutate(); }}>
      <p className="rfp-dialog-lead">{product ? `저장하면 ${product.version + 1}판이 됩니다. ${product.source === 'FILE' ? '기본 자료는 그대로 두고 이 판본을 비교에 씁니다.' : '이전 판본은 기록으로 남습니다.'}` : '등록한 제품은 다음 분석·다시 비교부터 자사 후보가 됩니다.'} 단종으로 바꾸면 비교 후보에서 빠집니다.</p>
      <div className="kv-form">
        <KvFormRow columns={3}>
          <Field label="제품 코드">{product ? <span className="ro">{product.product_code}</span> : <input aria-label="제품 코드" maxLength={64} value={code} onChange={e => setCode(e.target.value)} />}</Field>
          <Field label="계열"><select aria-label="계열" value={family} onChange={e => setFamily(e.target.value)}>{options.own_families.map(f => <option key={f} value={f}>{label(FAMILY_LABEL, f)}</option>)}</select></Field>
          <Field label="상태"><select aria-label="상태" value={life} onChange={e => setLife(e.target.value)}>{options.lifecycle_statuses.map(s => <option key={s} value={s}>{label(LIFECYCLE_LABEL, s)}</option>)}</select></Field>
        </KvFormRow>
        <KvFormRow columns={1}><Field label="적용 규격"><input aria-label="적용 규격" placeholder="쉼표로 나눠 적습니다. 예: IEC 60947-2, UL 489" value={standards} onChange={e => setStandards(e.target.value)} /></Field></KvFormRow>
      </div>
      <fieldset className="rfp-catalog-rows">
        <legend>인증 <small>인증 표시는 규격 이름만 있는 인증이라 충족을 확정하지 않습니다.</small></legend>
        <table className="kv-grid free"><thead><tr><th>규격</th><th style={{ width: 140 }}>종류</th><th style={{ width: 170 }}>근거</th><th style={{ width: 130 }}>변형 모델</th><th style={{ width: 46 }} /></tr></thead><tbody>
          {certs.map((c, i) => <tr key={i}>
            <td><input aria-label={`인증 ${i + 1} 규격`} maxLength={200} value={c.standard} onChange={e => setCerts(update(certs, i, { standard: e.target.value }))} /></td>
            <td><select aria-label={`인증 ${i + 1} 종류`} value={c.kind} onChange={e => setCerts(update(certs, i, { kind: e.target.value as typeof c.kind }))}>{options.own_certification_kinds.map(k => <option key={k} value={k}>{label(CERT_KIND_LABEL, k)}</option>)}</select></td>
            <td><input aria-label={`인증 ${i + 1} 근거`} maxLength={200} placeholder="제품 목록 등록" value={c.evidence_id} onChange={e => setCerts(update(certs, i, { evidence_id: e.target.value }))} /></td>
            <td><input aria-label={`인증 ${i + 1} 변형 모델`} maxLength={200} value={c.variant} onChange={e => setCerts(update(certs, i, { variant: e.target.value }))} /></td>
            <td className="c"><button type="button" className="kb sm" onClick={() => setCerts(certs.filter((_c, j) => j !== i))}>삭제</button></td>
          </tr>)}
        </tbody></table>
        <button type="button" className="kb" onClick={() => setCerts([...certs, { standard: '', kind: 'MARK', evidence_id: '', variant: '' }])}>인증 줄 추가</button>
      </fieldset>
      <FactRows rows={facts} onChange={setFacts} options={options} />
      <SaveBar problem={problem} pending={mutation.isPending} error={mutation.error} onClose={onClose} onReload={reload} saveLabel={product ? '수정 저장' : '등록'} />
    </form>
  </KvModal>;
}

type ProductRow = { product_code: string; model: string; scope: string[] };
type CertRow = { standard: string; models: string; kind: string; as_of: string; valid_until: string; evidence_id: string };
const tri = (value: boolean | null) => value == null ? '' : String(value);
const fromTri = (value: string) => value === '' ? null : value === 'true';

function PartnerForm({ options, partner, taken, onClose, onSaved }: { options: CatalogOptions; partner?: Partner; taken: string[]; onClose: () => void; onSaved: (code: string) => void }) {
  const p = partner?.profile;
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const [code, setCode] = useState(p?.candidate_code ?? nextPartnerCode(taken));
  const [name, setName] = useState(p?.display_name ?? '');
  const [maker, setMaker] = useState(p?.manufacturer_name ?? '');
  const [family, setFamily] = useState(p?.product_family ?? options.partner_families[0] ?? '');
  const [regions, setRegions] = useState(p?.supply_regions.join(', ') ?? 'KR');
  const [products, setProducts] = useState<ProductRow[]>(p?.products.map(x => ({ ...x })) ?? [{ product_code: '', model: '', scope: ['SUPPLY'] }]);
  const [integrator, setIntegrator] = useState({ assembly: tri(p?.integrator_capable.assembly ?? null), test: tri(p?.integrator_capable.test ?? null), integration: tri(p?.integrator_capable.integration ?? null), warranty: tri(p?.integrator_capable.warranty ?? null) });
  const [facts, setFacts] = useState<FactRow[]>(p?.spec_facts.map(factRow) ?? []);
  const [certs, setCerts] = useState<CertRow[]>(p?.certifications.map(c => ({ standard: c.standard, models: c.applicable_models.join(', '), kind: c.kind, as_of: c.as_of, valid_until: c.valid_until ?? '', evidence_id: c.evidence_id })) ?? []);
  const [weeks, setWeeks] = useState(p?.lead_time ? String(p.lead_time.value) : '');
  const [exWorks, setExWorks] = useState(p?.lead_time?.ex_works ?? false);
  const [warranty, setWarranty] = useState(p?.warranty ?? '');
  const [tasks, setTasks] = useState(p?.similar_tasks.join('\n') ?? '');
  const [status, setStatus] = useState(p?.confirmation.status ?? 'UNCONFIRMED');
  const [questions, setQuestions] = useState(p?.confirmation.follow_up_questions.join('\n') ?? '');
  const [notes, setNotes] = useState(p?.notes ?? '');
  const codes = products.map(x => x.product_code.trim());
  const models = new Set(products.map(x => x.model.trim()));
  const body: PartnerInput = {
    expected_version: p?.profile_version ?? 0, display_name: name.trim(), manufacturer_name: maker.trim(), product_family: family,
    products: products.map(x => ({ product_code: x.product_code.trim(), model: x.model.trim(), scope: x.scope as PartnerInput['products'][number]['scope'] })),
    integrator_capable: { assembly: fromTri(integrator.assembly), test: fromTri(integrator.test), integration: fromTri(integrator.integration), warranty: fromTri(integrator.warranty) },
    spec_facts: facts.map(f => ({ ...factBody(f), product_code: f.product_code })),
    certifications: certs.map(c => ({ standard: c.standard.trim(), applicable_models: list(c.models), kind: c.kind as 'LISTED', as_of: c.as_of, valid_until: c.valid_until || null, evidence_id: c.evidence_id.trim() || '제품 목록 등록' })),
    supply_regions: list(regions), similar_tasks: lines(tasks), lead_time: weeks ? { value: Number(weeks), unit: 'weeks', ex_works: exWorks } : null,
    warranty: warranty.trim() || null, notes: notes.trim() || null, confirmation_status: status as PartnerInput['confirmation_status'], follow_up_questions: lines(questions),
  };
  const problem = !/^[A-Z]-[A-Z]$/.test(code) ? "협력사 코드는 'R-A'처럼 대문자-대문자입니다."
    : !p && taken.includes(code) ? '이미 있는 협력사 코드입니다.'
      : !body.display_name || !body.manufacturer_name ? '협력사 이름과 제조사를 넣으세요.'
        : !body.supply_regions.length ? '공급 지역을 하나 이상 넣으세요.'
          : products.some(x => !x.product_code.trim() || !x.model.trim() || !x.scope.length) ? '제품 줄마다 제품 코드, 모델, 범위를 넣으세요.'
            : new Set(codes).size !== codes.length ? '제품 코드가 겹칩니다.'
              : weeks && !(Number.isInteger(Number(weeks)) && Number(weeks) > 0) ? '납기는 1 이상의 주 수로 넣으세요.'
                : certs.some(c => !c.standard.trim() || !list(c.models).length || !c.as_of) ? '인증 줄마다 규격, 적용 모델, 확인일을 넣으세요.'
                  : certs.some(c => list(c.models).some(m => !models.has(m))) ? '인증 적용 모델은 위 제품 줄의 모델이어야 합니다.'
                    : facts.some(f => f.product_code && !codes.includes(f.product_code)) ? '사양의 제품 코드가 제품 줄에 없습니다.'
                      : factProblem(facts, true);
  const { mutation, reload } = useSave(() => savePartner(code, body), ['catalog-partners'], () => { onSaved(code); onClose(); });
  return <KvModal title={p ? `협력사 수정 · ${p.candidate_code} ${p.display_name}` : '협력사 등록'} onClose={onClose} className="rfp-dialog rfp-catalog-dialog" keepOnBackdrop>
    <form onSubmit={e => { e.preventDefault(); if (!problem) mutation.mutate(); }}>
      <p className="rfp-dialog-lead">{p ? `저장하면 ${p.profile_version + 1}판이 됩니다. 이전 판본으로 매긴 등급은 기록으로 남지만 현행 후보 화면에서는 빠지고, 다음 분석·다시 채점 때 새 판본으로 다시 매깁니다. 그 등급에 기댄 보완 선택은 재검토 대상이 됩니다.` : '등록한 협력사는 다음 분석·다시 채점부터 모든 문서의 후보가 됩니다. 제품 계열이 맞는 요구만 채점합니다.'}</p>
      <div className="kv-form">
        <KvFormRow columns={3}>
          <Field label="코드">{p ? <span className="ro">{p.candidate_code}</span> : <input aria-label="협력사 코드" maxLength={3} value={code} onChange={e => setCode(e.target.value.toUpperCase())} />}</Field>
          <Field label="협력사"><input aria-label="협력사 이름" maxLength={200} value={name} onChange={e => setName(e.target.value)} /></Field>
          <Field label="제조사"><input aria-label="제조사" maxLength={200} value={maker} onChange={e => setMaker(e.target.value)} /></Field>
        </KvFormRow>
        <KvFormRow columns={3}>
          <Field label="제품 계열"><select aria-label="제품 계열" value={family} onChange={e => setFamily(e.target.value)}>{options.partner_families.map(f => <option key={f} value={f}>{f}</option>)}</select></Field>
          <Field label="공급 지역"><input aria-label="공급 지역" placeholder="쉼표로 나눔. 예: KR, US" value={regions} onChange={e => setRegions(e.target.value)} /></Field>
          <Field label="납기(주)"><input aria-label="납기 주" inputMode="numeric" value={weeks} onChange={e => setWeeks(e.target.value.replace(/\D/g, ''))} /><label className="rfp-catalog-check"><input type="checkbox" checked={exWorks} onChange={e => setExWorks(e.target.checked)} />공장 출하</label></Field>
        </KvFormRow>
        <KvFormRow columns={4}>
          {(['assembly', 'test', 'integration', 'warranty'] as const).map(key => <Field key={key} label={{ assembly: '조립', test: '시험', integration: '통합', warranty: '통합 보증' }[key]}>
            <select aria-label={`통합 책임 ${key}`} value={integrator[key]} onChange={e => setIntegrator({ ...integrator, [key]: e.target.value })}><option value="">미확인</option><option value="true">가능</option><option value="false">불가</option></select>
          </Field>)}
        </KvFormRow>
        <KvFormRow columns={3}>
          <Field label="보증"><input aria-label="보증" maxLength={200} placeholder="예: 납품 후 24개월" value={warranty} onChange={e => setWarranty(e.target.value)} /></Field>
          <Field label="업체 확인"><select aria-label="업체 확인" value={status} onChange={e => setStatus(e.target.value as typeof status)}>{Object.entries(CONFIRMATION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <Field label="확인일"><span className="ro">{today}(저장한 날)</span></Field>
        </KvFormRow>
      </div>
      <fieldset className="rfp-catalog-rows">
        <legend>제품 <small>제품 코드는 협력사끼리 겹칠 수 없습니다.</small></legend>
        <table className="kv-grid free"><thead><tr><th style={{ width: 170 }}>제품 코드</th><th>모델</th><th style={{ width: 330 }}>범위</th><th style={{ width: 46 }} /></tr></thead><tbody>
          {products.map((x, i) => <tr key={i}>
            <td><input aria-label={`제품 ${i + 1} 코드`} maxLength={64} value={x.product_code} onChange={e => setProducts(update(products, i, { product_code: e.target.value }))} /></td>
            <td><input aria-label={`제품 ${i + 1} 모델`} maxLength={200} value={x.model} onChange={e => setProducts(update(products, i, { model: e.target.value }))} /></td>
            <td><span className="rfp-catalog-scopes">{options.scopes.map(s => <label key={s} className="rfp-catalog-check"><input type="checkbox" checked={x.scope.includes(s)} onChange={e => setProducts(update(products, i, { scope: e.target.checked ? [...x.scope, s] : x.scope.filter(v => v !== s) }))} />{label(SCOPE_LABEL, s)}</label>)}</span></td>
            <td className="c"><button type="button" className="kb sm" disabled={products.length === 1} onClick={() => setProducts(products.filter((_x, j) => j !== i))}>삭제</button></td>
          </tr>)}
        </tbody></table>
        <button type="button" className="kb" onClick={() => setProducts([...products, { product_code: '', model: '', scope: ['SUPPLY'] }])}>제품 줄 추가</button>
      </fieldset>
      <FactRows rows={facts} onChange={setFacts} options={options} products={codes} />
      <fieldset className="rfp-catalog-rows">
        <legend>인증 <small>등록(Listed)·시험 성적서만 받습니다. 적용 모델은 쉼표로 나눕니다.</small></legend>
        <table className="kv-grid free"><thead><tr><th>규격</th><th style={{ width: 150 }}>적용 모델</th><th style={{ width: 120 }}>종류</th><th style={{ width: 130 }}>확인일</th><th style={{ width: 130 }}>유효기한</th><th style={{ width: 120 }}>근거</th><th style={{ width: 46 }} /></tr></thead><tbody>
          {certs.map((c, i) => <tr key={i}>
            <td><input aria-label={`협력사 인증 ${i + 1} 규격`} maxLength={200} value={c.standard} onChange={e => setCerts(update(certs, i, { standard: e.target.value }))} /></td>
            <td><input aria-label={`협력사 인증 ${i + 1} 적용 모델`} value={c.models} onChange={e => setCerts(update(certs, i, { models: e.target.value }))} /></td>
            <td><select aria-label={`협력사 인증 ${i + 1} 종류`} value={c.kind} onChange={e => setCerts(update(certs, i, { kind: e.target.value }))}>{options.partner_certification_kinds.map(k => <option key={k} value={k}>{label(CERT_KIND_LABEL, k)}</option>)}</select></td>
            <td><input type="date" aria-label={`협력사 인증 ${i + 1} 확인일`} value={c.as_of} onChange={e => setCerts(update(certs, i, { as_of: e.target.value }))} /></td>
            <td><input type="date" aria-label={`협력사 인증 ${i + 1} 유효기한`} value={c.valid_until} onChange={e => setCerts(update(certs, i, { valid_until: e.target.value }))} /></td>
            <td><input aria-label={`협력사 인증 ${i + 1} 근거`} maxLength={200} placeholder="제품 목록 등록" value={c.evidence_id} onChange={e => setCerts(update(certs, i, { evidence_id: e.target.value }))} /></td>
            <td className="c"><button type="button" className="kb sm" onClick={() => setCerts(certs.filter((_c, j) => j !== i))}>삭제</button></td>
          </tr>)}
        </tbody></table>
        <button type="button" className="kb" onClick={() => setCerts([...certs, { standard: '', models: products[0]?.model ?? '', kind: options.partner_certification_kinds[0] ?? 'LISTED', as_of: today, valid_until: '', evidence_id: '' }])}>인증 줄 추가</button>
      </fieldset>
      <div className="kv-form rfp-catalog-texts">
        <KvFormRow columns={3}>
          <Field label="유사 실적"><textarea aria-label="유사 실적" rows={3} placeholder="한 줄에 하나" value={tasks} onChange={e => setTasks(e.target.value)} /></Field>
          <Field label="확인할 질문"><textarea aria-label="확인할 질문" rows={3} placeholder="한 줄에 하나" value={questions} onChange={e => setQuestions(e.target.value)} /></Field>
          <Field label="메모"><textarea aria-label="메모" rows={3} maxLength={2000} value={notes} onChange={e => setNotes(e.target.value)} /></Field>
        </KvFormRow>
      </div>
      <SaveBar problem={problem} pending={mutation.isPending} error={mutation.error} onClose={onClose} onReload={reload} saveLabel={p ? '수정 저장' : '등록'} />
    </form>
  </KvModal>;
}
