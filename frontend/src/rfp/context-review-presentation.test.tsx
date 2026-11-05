import { expect,it } from 'vitest';
import type { DocumentVersion,RequirementSummary } from '../api/rfp';
import type { ContextEnvelope } from '../api/rfp-context';
import { reviewPresentation, reviewRequirementLabel, reviewTargetName } from './context-review-presentation';
const doc={document_id:'d',document_version_id:'v'} as DocumentVersion;
const rows=[{requirement_id:'a',version:1},{requirement_id:'b',version:1},{requirement_id:'c',version:1}] as RequirementSummary[];
const data=()=>({project_id:'p',status:'CURRENT',version:1,source_fingerprint:'a',current_source_fingerprint:'a',source_refs:rows.map(row=>({requirement_id:row.requirement_id,version:1,document_id:'d',document_version_id:'v'})),result:{groups:[{entity_id:'parent',name:'계통',kind:'SYSTEM'},{entity_id:'one',name:'변압기',tag:'TR-A',site:'동관',kind:'EQUIPMENT',parent_id:'parent'},{entity_id:'two',name:'변압기',tag:'TR-B',site:'서관',kind:'EQUIPMENT',parent_id:'parent'}],assignments:[{requirement_id:'a',primary_category:'EQUIPMENT',group_ids:['one']},{requirement_id:'b',primary_category:'QUALITY',group_ids:['one','two']}],issues:[]}}) as unknown as ContextEnvelope;
it('keeps same-name equipment distinct and computes only explicit descendant unique unions',()=>{
 const d=data(),p=reviewPresentation('p',doc,[doc],rows,d);
 expect(p.targets.map(target=>target.rows.map(row=>row.requirement_id))).toEqual([['a','b'],['a','b'],['b'],['c']]);
 expect(p.uniqueCount).toBe(3);expect(p.membershipCount).toBe(3);
 expect(p.targets[1].name).toContain('TR-A · 동관');expect(p.targets[2].name).toContain('TR-B · 서관');
 expect(p.targets[0].includesChildren).toBe(true);
 expect(p.targets[1].key).not.toBe(p.targets[2].key);
});
it.each(['STALE','NOT_ANALYZED','wrong-project','fingerprint','requirement-version','document-id','missing-ref'] as const)('preserves every actual requirement as unorganized for %s',failure=>{
 const d=data();
 if(failure==='STALE'||failure==='NOT_ANALYZED')d.status=failure;
 if(failure==='wrong-project')d.project_id='other';
 if(failure==='fingerprint')d.current_source_fingerprint='other';
 if(failure==='requirement-version')d.source_refs[0].version=2;
 if(failure==='document-id')d.source_refs[0].document_id='other';
 if(failure==='missing-ref')d.source_refs.pop();
 const p=reviewPresentation('p',doc,[doc],rows,d);
 expect(p.current).toBe(false);expect(p.targets).toHaveLength(1);expect(p.targets[0].name).toBe('미정리 요구');expect(p.targets[0].rows).toEqual(rows);
});
it('uses readable source labels and never leaks opaque UUID/OCR identifiers',()=>{
 const id='11111111-1111-4111-8111-111111111111';
 expect(reviewRequirementLabel('1234abcd:2.1.A:3',0)).toBe('조항 2.1.A · 요구 3');
 expect(reviewRequirementLabel(id,2)).toBe('요구 3');
 expect(reviewRequirementLabel('1234abcd:OCR-PDF-P0001.1:1',2,1)).toBe('원문 1쪽 · 요구 3');
 expect(reviewRequirementLabel('R-01',0)).toBe('R-01');
 const group={...data().result!.groups[1],name:`변압기 ${id}`,tag:id};
 expect(reviewTargetName(group,[group])).toBe('변압기 · 동관');
});
it('unrelated relation candidates never merge equipment',()=>{
 const d=data();d.result!.assignments[0].relations=[{group_id:'two',relation:'SAME_EQUIPMENT'}];
 const p=reviewPresentation('p',doc,[doc],rows,d);
 expect(p.targets[2].rows.map(row=>row.requirement_id)).toEqual(['b']);
});
