'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {run, MARKER} = require('../scripts/routine_review.cjs');
const {schema} = require('../scripts/ai_review.cjs');

const repository = 'LS-Final-Project/ls-final-project';
const head = '1'.repeat(40), base = '2'.repeat(40);
const url = 'https://api.anthropic.com/v1/claude_code/routines/trig_fixture/fire';

function fixture() {
  const pr = {number: 12, state: 'open', draft: false, head: {sha: head, repo: {full_name: repository}}, base: {sha: base}};
  const context = {repo: {owner: 'LS-Final-Project', repo: 'ls-final-project'},
    payload: {pull_request: structuredClone(pr)}, runId: 123, runAttempt: 1};
  const prepared = {files: ['example.py'], diff: 'diff --git a/example.py b/example.py\n-old\n+new\n', schema};
  let clock = Date.parse('2026-11-01T12:00:00Z');
  const f = {context, prepared, url, token: 'fixture-routine-token', pr,
    requests: [], published: [], edits: [], failures: [], reads: 0, result: undefined,
    now: () => clock, sleep: async ms => {clock += ms;},
    advance: ms => {clock += ms;}};
  f.review = () => ({head_sha: head, base_sha: base, review_complete: true,
    reviewed_files: ['example.py'], summary: '전체 diff 검토', findings: []});
  f.comment = () => ({id: 42, user: {type: 'Bot', id: 209825114},
    performed_via_github_app: {id: 1236702},
    created_at: '2026-11-01T12:00:00Z', updated_at: '2026-11-01T12:00:00Z',
    body: `${MARKER}\n${JSON.stringify({...f.payload.request, review: f.review()})}`});
  f.comments = () => [f.comment()];
  f.fetchImpl = async (target, options) => {
    f.requests.push({target, options});
    f.payload = JSON.parse(JSON.parse(options.body).text);
    return {status: 200, json: async () => ({type: 'routine_fire', claude_code_session_id: 'session_fixture'})};
  };
  f.github = {rest: {pulls: {get: async () => ({data: structuredClone(pr)})}, issues: {
    listComments() {}, getComment: async () => ({data: f.comment()}),
    createComment: async value => {f.published.push(value); return {data: {id: 99}};},
    updateComment: async value => {f.edits.push(value);},
  }}, paginate: async (_method, params) => {f.reads++; f.listParams = params; return f.comments();}};
  f.core = {setFailed: value => f.failures.push(value), summary: {
    addHeading() {return this;}, addRaw() {return this;}, async write() {},
  }};
  return f;
}

test('starts once; sends complete diff/schema without secrets, then authenticates a bound result', async () => {
  const f = fixture();
  await run(f);
  assert.equal(f.requests.length, 1);
  const {target, options} = f.requests[0];
  assert.equal(target, url);
  assert.equal(options.method, 'POST');
  assert.equal(options.redirect, 'error');
  assert.equal(options.headers['anthropic-version'], '2026-10-21');
  assert.equal(options.headers.Authorization, `Bearer ${f.token}`);
  assert.ok(!options.body.includes(f.token));
  assert.ok(!options.body.includes(url));
  assert.equal(f.payload.kind, 'routine-fire-payload');
  assert.equal(f.payload.diff, f.prepared.diff);
  assert.equal(f.payload.head_sha, head);
  assert.equal(f.payload.base_sha, base);
  assert.deepEqual(f.payload.reviewed_files, f.prepared.files);
  assert.equal(f.payload.result_marker, MARKER);
  assert.deepEqual(f.payload.result_schema.properties.review, schema);
  assert.match(f.payload.request.request_id, /^[0-9a-f-]{36}$/);
  assert.equal(f.payload.request.run_id, '123');
  assert.equal(f.payload.request.run_attempt, '1');
  assert.equal(f.reads, 2);
  assert.equal(f.listParams.per_page, 100);
  assert.equal(f.published.length, 1);
  assert.match(f.published[0].body, /\*\*통과\*\*/);
});

test('requests and run attempts have independent unpredictable IDs', async () => {
  const a = fixture(), b = fixture(); b.context.runAttempt = 2;
  await run(a); await run(b);
  assert.notEqual(a.payload.request.request_id, b.payload.request.request_id);
  assert.equal(b.payload.request.run_attempt, '2');
});

test('rejects unofficial URLs before transmitting any secret', async () => {
  for (const bad of [undefined, 'http://api.anthropic.com/v1/claude_code/routines/trig_x/fire',
    `${url}\n`, ` ${url}`,
    `${url}?x=1`, `${url}#fragment`, `${url}/`, url.replace('api.anthropic.com', 'api.anthropic.com.evil'),
    url.replace('api.anthropic.com', 'user@api.anthropic.com'), url.replace('api.anthropic.com', 'api.anthropic.com:443'),
    url.replace('/fire', '/read'), url.replace('trig_fixture', '..'), url.replace('trig_fixture', 'trig_%2f')]) {
    const f = fixture(); f.url = bad;
    await assert.rejects(() => run(f));
    assert.equal(f.requests.length, 0);
    assert.equal(f.published.length, 0);
  }
});

test('workflow uses trusted checkout and only prepares then calls the Routine verifier', async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const workflow = fs.readFileSync(path.join(__dirname, '../workflows/claude-code-review.yml'), 'utf8');
  assert.match(workflow, /ref: \$\{\{ github.event.repository.default_branch \}\}/);
  assert.match(workflow, /persist-credentials: false/);
  assert.match(workflow, /retries: 0/);
  assert.ok(!/claude_code_oauth_token|claude-code-action|continue-on-error|id-token:/.test(workflow));
  const script = workflow.split('          script: |\n')[1].split('\n')
    .map(line => line.replace(/^            /, '')).join('\n');
  const calls = [];
  const prepared = {files: ['example.py'], diff: 'entire diff'};
  const load = name => name.endsWith('/ai_review.cjs')
    ? {prepare: async () => {calls.push('prepare'); return prepared;}}
    : {run: async args => {calls.push('routine'); assert.equal(args.prepared, prepared);}};
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFunction('require', 'github', 'context', 'core', script)(load, {}, {}, {});
  assert.deepEqual(calls, ['prepare', 'routine']);
});

test('oversized full diff fails without truncation or starting a session', async () => {
  const f = fixture(); f.prepared.diff = '한'.repeat(65536);
  await assert.rejects(() => run(f), /65536/);
  assert.equal(f.requests.length, 0);
});

test('no POST retries for timeouts, redirects, rate limits or bad responses; no secret in errors', async () => {
  for (const failure of ['network', 'invalid-json', 'invalid-session', 301, 401, 403, 429, 500, 503]) {
    const f = fixture();
    f.fetchImpl = async () => {
      f.requests.push({});
      if (failure === 'network') throw new Error(`${f.url} ${f.token}`);
      return {status: typeof failure === 'number' ? failure : 200, json: async () => {
        if (failure === 'invalid-json') throw new Error(f.token);
        return {type: 'error', error: {message: f.token}};
      }};
    };
    await assert.rejects(() => run(f), error => !error.message.includes(f.token) && !error.message.includes(url));
    assert.equal(f.requests.length, 1);
    assert.equal(f.reads, 0);
    assert.equal(f.published.length, 0);
  }
});

test('fire success alone, missing results and matching human/other-App comments never pass', async () => {
  for (const change of [() => [], c => [{...c, user: {id: 209825114, type: 'User'}}],
    c => [{...c, user: {id: 41898282, type: 'Bot'}}],
    c => [{...c, user: {id: 12345, type: 'User'}}],
    c => [{...c, performed_via_github_app: {id: 15368}}],
    c => [{...c, performed_via_github_app: null}]]) {
    const f = fixture(); f.comments = () => change(f.comment());
    await assert.rejects(() => run(f), /기한/);
    assert.equal(f.requests.length, 1);
    assert.equal(f.published.length, 0);
  }
});

test('rejects an authenticated result with wrong request binding, timestamps or edits', async () => {
  for (const key of ['run_id', 'run_attempt', 'repository', 'pr_number', 'deadline']) {
    const f = fixture(); const comment = f.comment;
    f.comment = () => {const c = comment(); const v = JSON.parse(c.body.slice(MARKER.length + 1));
      v[key] = 'wrong'; return {...c, body: `${MARKER}\n${JSON.stringify(v)}`};};
    await assert.rejects(() => run(f), /일치/);
    assert.equal(f.published.length, 0);
  }
  for (const change of [
    {created_at: '2026-11-01T11:59:59Z', updated_at: '2026-11-01T11:59:59Z'},
    {created_at: '2026-11-01T12:16:00Z', updated_at: '2026-11-01T12:16:00Z'},
    {updated_at: '2026-11-01T12:00:01Z'}, {created_at: 'invalid'},
    {created_at: '2026-11-01T12:01:00Z', updated_at: '2026-11-01T12:01:00Z'},
  ]) {
    const f = fixture(); const comment = f.comment; f.comment = () => ({...comment(), ...change});
    await assert.rejects(() => run(f), /일치/);
    assert.equal(f.published.length, 0);
  }
});

test('old requests cannot be replayed; malformed trusted JSON is never success', async () => {
  for (const body of [() => `${MARKER}\n{broken`,
    f => `${MARKER}\n${JSON.stringify({...f.payload.request, request_id: 'old', review: f.review()})}`]) {
    const f = fixture(); const comment = f.comment; f.comment = () => ({...comment(), body: body(f)});
    await assert.rejects(() => run(f));
    assert.equal(f.published.length, 0);
  }
});

test('duplicate same-request comments, including a duplicate on recheck, fail closed', async () => {
  for (const after of [0, 1]) {
    const f = fixture(); f.comments = () => f.reads > after ? [f.comment(), {...f.comment(), id: 43}] : [f.comment()];
    await assert.rejects(() => run(f), /중복/);
    assert.equal(f.published.length, 0);
  }
});

test('comment replacement, deletion or edit during final verification never publishes success', async () => {
  for (const mode of ['delete', 'replace', 'edit', 'get-edit']) {
    const f = fixture();
    f.comments = () => f.reads === 1 || mode === 'get-edit' ? [f.comment()] : mode === 'delete' ? [] :
      [{...f.comment(), ...(mode === 'replace' ? {id: 43} : {updated_at: '2026-11-01T12:00:01Z'})}];
    if (mode === 'get-edit') f.github.rest.issues.getComment = async () => ({data: {...f.comment(), updated_at: '2026-11-01T12:00:01Z'}});
    await assert.rejects(() => run(f));
    assert.equal(f.published.length, 0);
  }
});

test('results arriving at the deadline are rejected', async () => {
  const f = fixture(); f.github.paginate = async () => {f.advance(15 * 60 * 1000); return [f.comment()];};
  await assert.rejects(() => run(f), /기한/);
  assert.equal(f.published.length, 0);
});

test('incomplete, stale-SHA and partial-file results cannot bypass existing validation', async () => {
  for (const change of [{review_complete: false}, {head_sha: '3'.repeat(40)}, {base_sha: '3'.repeat(40)},
    {reviewed_files: []}, {findings: [{severity: 'LOW', path: 'example.py', line: 1, description: 'x'}]}]) {
    const f = fixture(); const review = f.review; f.review = () => ({...review(), ...change});
    await assert.rejects(() => run(f));
    assert.equal(f.published.length, 0);
  }
});

test('P0-P2 block through finish, P3 remains informational', async () => {
  for (const severity of ['P0', 'P1', 'P2', 'P3']) {
    const f = fixture(); const review = f.review;
    f.review = () => ({...review(), findings: [{severity, path: 'example.py', line: 2, description: '재현 가능한 결함'}]});
    await run(f);
    assert.equal(f.failures.length, severity === 'P3' ? 0 : 1);
    assert.equal(f.published.length, 1);
  }
});

test('foreign repository/fork, stale SHA, closed or draft PR cannot start a session', async () => {
  for (const change of [f => {f.context.repo.repo = 'other';}, f => {f.pr.head.repo.full_name = 'fork/repo';},
    f => {f.pr.head.sha = '3'.repeat(40);}, f => {f.pr.base.sha = '3'.repeat(40);},
    f => {f.pr.state = 'closed';}, f => {f.pr.draft = true;}]) {
    const f = fixture(); change(f);
    await assert.rejects(() => run(f)); assert.equal(f.requests.length, 0);
  }
});

test('head/base changes while waiting or publishing invalidate the review', async () => {
  for (const change of ['head', 'base', 'publish']) {
    const f = fixture(); const sleep = f.sleep;
    f.sleep = async ms => {await sleep(ms); if (change !== 'publish') f.pr[change].sha = '3'.repeat(40);};
    if (change === 'publish') f.github.rest.issues.createComment = async value => {
      f.published.push(value); f.pr.head.sha = '3'.repeat(40); return {data: {id: 99}};
    };
    await assert.rejects(() => run(f));
    if (change === 'publish') {assert.equal(f.edits.length, 1); assert.match(f.edits[0].body, /검토 무효/);}
    else assert.equal(f.published.length, 0);
  }
});
