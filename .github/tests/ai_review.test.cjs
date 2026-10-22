'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { prepare, validate, finish } = require('../scripts/ai_review.cjs');
const head = '1'.repeat(40), base = '2'.repeat(40);
const files = ['backend/example.py', 'tests/test_example.py'];

/** 합성 모델 결과를 만든다. Args: overrides — 변경할 필드. Returns: 결과 객체. */
function result(overrides = {}) {
  return { head_sha: head, base_sha: base, review_complete: true, reviewed_files: files,
    summary: '변경된 계약과 회귀 위험을 검토했습니다.', findings: [], ...overrides };
}

/** 검토 판정 환경을 만든다. Args: overrides — 결과 변경. Returns: API와 출력 기록. */
function fixture(overrides = {}) {
  const pr = {number: 12, state: 'open', draft: false, head: {sha: head}, base: {sha: base}, changed_files: 2};
  const comments = [], failed = [], outputs = {};
  const summary = {addHeading() { return this; }, addRaw() {return this; }, async write() {}};
  const context = {repo: {owner: 'team', repo: 'project'}, payload: {pull_request: structuredClone(pr)}, runId: 123};
  const github = {rest: {pulls: {
    async get() {return {data: structuredClone(pr)};}, async listFiles() {},
  }, issues: {async createComment(comment) { comments.push(comment); }}},
  async paginate() {return files.map(filename => ({filename}));}};
  const core = {summary, setFailed(message) {failed.push(message);}, setOutput(key, value) {outputs[key] = value;}};
  return {github, context, core, raw: JSON.stringify(result(overrides)), files: JSON.stringify(files),
    conclusion: 'success', pr, comments, failed, outputs};
}

test('prepare binds the schema to the complete changed-file manifest', async () => {
  const f = fixture();
  await prepare(f);
  assert.deepEqual(JSON.parse(f.outputs.files), files);
  assert.equal(JSON.parse(f.outputs.schema).additionalProperties, false);
  f.pr.changed_files = 3;
  await assert.rejects(() => prepare(f));
});

test('rejects missing, incomplete, stale, empty-summary and partially reviewed results', () => {
  for (const raw of ['', 'null', '{}', ...[
    {review_complete: false}, {head_sha: '0'.repeat(40)}, {base_sha: '0'.repeat(40)},
    {summary: '  '}, {reviewed_files: [files[0]]}, {reviewed_files: [files[0], files[0]]},
    {reviewed_files: [...files, 'other.py']}, {findings: null},
  ].map(change => JSON.stringify(result(change)))]) {
    assert.throws(() => validate(raw, {head, base, files}), raw);
  }
});

test('rejects invalid severity, location and empty findings', () => {
  for (const finding of [null, {}, {severity: 'LOW', path: files[0], line: 1, description: '오류'},
    {severity: 'P1', path: 'other.py', line: 1, description: '오류'},
    {severity: 'P1', path: files[0], line: 0, description: '오류'},
    {severity: 'P1', path: files[0], line: 1, description: '  '}]) {
    assert.throws(() => validate(JSON.stringify(result({findings: [finding]})), {head, base, files}));
  }
});

test('publishes explicit head/base and passes completed reviews with no blocking finding', async () => {
  for (const findings of [[], [{severity: 'P3', path: files[0], line: 2, description: '참고 사항'}]]) {
    const f = fixture({findings});
    await finish(f);
    assert.equal(f.comments.length, 1);
    assert.ok(f.comments[0].body.includes(head));
    assert.ok(f.comments[0].body.includes(base));
    assert.ok(f.comments[0].body.includes('**통과**'));
    assert.equal(f.failed.length, 0);
  }
});

test('P0, P1 and P2 findings fail the check after publishing actionable feedback', async () => {
  for (const severity of ['P0', 'P1', 'P2']) {
    const f = fixture({findings: [{severity, path: files[0], line: 2, description: '재현 가능한 결함'}]});
    await finish(f);
    assert.equal(f.failed.length, 1);
    assert.ok(f.comments[0].body.includes('수정 필요'));
    assert.ok(f.comments[0].body.includes('재현 가능한 결함'));
  }
});

test('execution failure and changed head/base never publish passing comments', async () => {
  for (const change of ['failure', 'head', 'base', 'closed', 'draft']) {
    const f = fixture();
    if (change === 'failure') f.conclusion = '';
    if (change === 'head') f.pr.head.sha = '3'.repeat(40);
    if (change === 'base') f.pr.base.sha = '3'.repeat(40);
    if (change === 'closed') f.pr.state = 'closed';
    if (change === 'draft') f.pr.draft = true;
    await assert.rejects(() => finish(f));
    assert.equal(f.comments.length, 0);
  }
});

test('a push while the comment is published invalidates the run', async () => {
  const f = fixture();
  f.github.rest.issues.createComment = async comment => {f.comments.push(comment); f.pr.head.sha = '3'.repeat(40);};
  await assert.rejects(() => finish(f));
});

test('AI text cannot mention teammates and comment publishing errors propagate', async () => {
  const f = fixture({summary: '@jongddugi <tag> 검토 결과'});
  await finish(f);
  assert.ok(!f.comments[0].body.includes('@jongddugi'));
  assert.ok(f.comments[0].body.includes('&lt;tag&gt;'));
  const broken = fixture();
  broken.github.rest.issues.createComment = async () => {throw new Error('게시 권한 없음');};
  await assert.rejects(() => finish(broken), /게시 권한 없음/);
});
