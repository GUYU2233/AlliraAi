import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('published declarations retain legacy DTOs and reject invalid new auth, patch and import contracts', { timeout: 30000 }, () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const compiler = fileURLToPath(new URL('../node_modules/typescript/bin/tsc', import.meta.url));
  const result = spawnSync(process.execPath, [
    compiler, '--noEmit', '--strict', '--skipLibCheck', '--target', 'ES2022',
    '--module', 'NodeNext', '--moduleResolution', 'NodeNext', '--lib', 'ES2022,DOM,DOM.Iterable',
    'test/contracts.mts',
  ], { cwd: root, encoding: 'utf8', timeout: 25000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0, `${result.stdout}${result.stderr}`);
});
