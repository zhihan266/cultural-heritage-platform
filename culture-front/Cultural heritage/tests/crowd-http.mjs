// Every run owns fresh HTTP processes and a disposable SQLite database.
import { spawn, spawnSync } from 'node:child_process'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
const root = fileURLToPath(new URL('..', import.meta.url))
const backend = path.resolve(root, '../../Cultural heritage ai')
const python = process.env.PYTHON || path.join(backend, '.venv/Scripts/python.exe')
const children = []
async function port() {
  const server = createServer()
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const value = server.address().port
  await new Promise((resolve) => server.close(resolve))
  return value
}
function launch(command, args, env) {
  const child = spawn(command, args, {
    cwd: root,
    env: { ...process.env, ...env },
    windowsHide: true,
    stdio: 'inherit',
  })
  children.push(child)
  return child
}
async function ready(url, child) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error('Test host exited before becoming ready')
    try {
      if ((await fetch(url, { signal: AbortSignal.timeout(500) })).ok) return
    } catch {
      /* Starting. */
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error('Test host did not become ready: ' + url)
}
try {
  const backendPort = await port(),
    frontendPort = await port()
  const api = launch(python, ['-u', 'tests/crowd_server.py', backend, String(backendPort)])
  await ready(`http://127.0.0.1:${backendPort}/openapi.json`, api)
  const frontend = launch(
    process.execPath,
    [
      'node_modules/vite/bin/vite.js',
      '--host',
      '127.0.0.1',
      '--port',
      String(frontendPort),
      '--strictPort',
    ],
    { BACKEND_URL: `http://127.0.0.1:${backendPort}` },
  )
  await ready(`http://127.0.0.1:${frontendPort}`, frontend)
  const test = launch(process.execPath, ['tests/crowd-http-browser.mjs'], {
    FRONTEND_URL: `http://127.0.0.1:${frontendPort}`,
  })
  process.exitCode = await new Promise((resolve) => test.on('exit', (code) => resolve(code ?? 1)))
} finally {
  for (const child of children.reverse())
    if (child.exitCode === null && child.pid) {
      if (process.platform === 'win32')
        spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
          windowsHide: true,
          stdio: 'ignore',
        })
      else child.kill('SIGTERM')
    }
}
