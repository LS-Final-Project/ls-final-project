const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {test} = require('node:test');

const workflow = fs.readFileSync(path.join(__dirname, '../workflows/application-checks.yml'), 'utf8');
const childEnv = {...process.env};
delete childEnv.NODE_TEST_CONTEXT;
const infrastructure = workflow.split('  infrastructure:\n')[1].split('  evaluation:\n')[0];

test('협업 검사 명령이 실제 실패를 반환하고 최종 결과가 이를 차단한다', () => {
  const command = infrastructure.match(/name: Test PR automation\n\s+run: (.+)/)?.[1];
  assert.ok(command, '협업 검사는 infrastructure에서 실행해야 한다');
  assert.ok(!infrastructure.includes('continue-on-error:'), '검사 실패를 무시하지 않는다');
  assert.equal((infrastructure.match(/uv run --frozen python -m unittest discover -s scripts\/tests/g) || []).length, 1);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'kan180-ci-'));
  try {
    fs.mkdirSync(path.join(directory, '.github/tests'), {recursive: true});
    fs.writeFileSync(path.join(directory, '.github/tests/failure.test.cjs'),
      "require('node:test').test('failure fixture', () => { throw new Error('expected failure'); });\n");
    const failed = spawnSync('bash', ['-e', '-c', command], {cwd: directory, env: childEnv, encoding: 'utf8'});
    assert.ifError(failed.error);
    assert.equal(failed.status, 1, failed.stderr);
    assert.match(failed.stdout + failed.stderr, /failure fixture/);

    const required = workflow.split('  required:\n')[1];
    assert.match(required, /if: always\(\)/);
    assert.match(required.match(/needs: \[(.+)\]/)[1], /\binfrastructure\b/);
    const script = required.split("<<'PYRESULT'\n")[1].split('          PYRESULT')[0]
      .split('\n').map(line => line.replace(/^          /, '')).join('\n');
    for (const result of ['failure', 'cancelled', 'success', 'skipped']) {
      const check = spawnSync('python3', ['-c', script], {
        env: {...childEnv, RESULTS: JSON.stringify({changes: {result: 'success'}, infrastructure: {result}})},
        encoding: 'utf8',
      });
      assert.ifError(check.error);
      assert.equal(check.status === 0, ['success', 'skipped'].includes(result), check.stderr);
    }
  } finally {
    fs.rmSync(directory, {recursive: true, force: true});
  }
});
