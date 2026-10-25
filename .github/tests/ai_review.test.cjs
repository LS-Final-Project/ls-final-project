'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
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
  const comments = [], edits = [], failed = [], outputs = {};
  const diff = {value: files.map(filename => `diff --git a/${filename} b/${filename}\n--- a/${filename}\n+++ b/${filename}\n@@ -1 +1 @@\n-old\n+new\n`).join('')};
  const summary = {addHeading() { return this; }, addRaw() {return this; }, async write() {}};
  const context = {repo: {owner: 'team', repo: 'project'}, payload: {pull_request: structuredClone(pr)}, runId: 123};
  const github = {rest: {pulls: {
    async get(params) {return {data: params?.mediaType?.format === 'diff' ? diff.value : structuredClone(pr)};}, async listFiles() {},
  }, issues: {
    async createComment(comment) { comments.push(comment); return {data: {id: 1}}; },
    async updateComment(comment) {edits.push(comment);},
  }},
  async paginate() {return files.map(filename => ({filename}));}};
  const core = {summary, setFailed(message) {failed.push(message);}, setOutput(key, value) {outputs[key] = value;}};
  return {github, context, core, raw: JSON.stringify(result(overrides)), files: JSON.stringify(files),
    conclusion: 'success', pr, diff, comments, edits, failed, outputs};
}

/** 검사 산출물만 정리한다. Args: f — 합성 검사 환경. Returns: 없음. */
function cleanDiff(f) {
  if (f.outputs.diff_file) fs.rmSync(path.dirname(f.outputs.diff_file), {recursive: true, force: true});
}

test('prepare binds the schema to the complete changed-file manifest', async () => {
  const f = fixture();
  try {
    await prepare(f);
    assert.deepEqual(JSON.parse(f.outputs.files), files);
    assert.equal(JSON.parse(f.outputs.schema).additionalProperties, false);
    assert.equal(fs.readFileSync(f.outputs.diff_file, 'utf8'), f.diff.value);
    f.pr.changed_files = 3;
    await assert.rejects(() => prepare(f));
  } finally {cleanDiff(f);}
});

test('168파일의 큰 diff를 잘림 없이 모델이 읽을 파일로 제공한다', async () => {
  const f = fixture();
  const many = Array.from({length: 168}, (_, i) => `tests/file_${i}.py`);
  f.pr.changed_files = many.length;
  f.github.paginate = async () => many.map(filename => ({filename}));
  f.diff.value = many.map(filename => `diff --git a/${filename} b/${filename}\n--- a/${filename}\n+++ b/${filename}\n@@ -1 +1 @@\n-old\n+${'합성 변경 '.repeat(1024)}\n`).join('');
  try {
    await prepare(f);
    assert.deepEqual(JSON.parse(f.outputs.files), many);
    assert.equal(fs.readFileSync(f.outputs.diff_file, 'utf8'), f.diff.value);
    assert.equal(fs.statSync(f.outputs.diff_file).mode & 0o777, 0o600);
    assert.equal(Number(f.outputs.diff_lines), f.diff.value.split('\n').length);
  } finally {cleanDiff(f);}
});

test('누락된 파일 diff나 읽는 동안 바뀐 SHA를 모델에게 넘기지 않는다', async () => {
  for (const invalid of ['', '<html>error</html>', `diff --git a/${files[0]} b/${files[0]}\n`]) {
    const f = fixture();
    f.diff.value = invalid;
    await assert.rejects(() => prepare(f));
    assert.equal(f.outputs.diff_file, undefined);
  }
  const f = fixture();
  const get = f.github.rest.pulls.get;
  f.github.rest.pulls.get = async params => {
    const response = await get(params);
    if (params?.mediaType?.format === 'diff') f.pr.head.sha = '3'.repeat(40);
    return response;
  };
  await assert.rejects(() => prepare(f));
  assert.equal(f.outputs.diff_file, undefined);
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
  f.github.rest.issues.createComment = async comment => {
    f.comments.push(comment); f.pr.head.sha = '3'.repeat(40); return {data: {id: 1}};
  };
  await assert.rejects(() => finish(f));
  assert.equal(f.edits.length, 1);
  assert.ok(f.edits[0].body.includes('검토 무효'));
  assert.ok(!f.edits[0].body.includes('**통과**'));
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
