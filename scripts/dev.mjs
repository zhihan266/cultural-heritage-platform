import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, openSync, closeSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const root = fileURLToPath(new URL('..', import.meta.url))
const backendRoot = join(root, 'Cultural heritage ai')
const frontendRoot = join(root, 'culture-front', 'Cultural heritage')
const backendPort = Number(process.env.BACKEND_PORT || 8000)
const frontendPort = Number(process.env.FRONTEND_PORT || 5173)
const backendUrl = `http://127.0.0.1:${backendPort}`
const frontendUrl = `http://127.0.0.1:${frontendPort}`
const owned = []

async function reachable(url, expected) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(4000) })
    if (!response.ok) return false
    const body = await response.text()
    return !expected || body.includes(expected)
  } catch { return false }
}

function launch(command, args, cwd, name, env = {}) {
  const logRoot = join(root, '.run')
  mkdirSync(logRoot, { recursive: true })
  const output = openSync(join(logRoot, `${name}.log`), 'a')
  const child = spawn(command, args, {
    cwd, env: { ...process.env, ...env, PYTHONDONTWRITEBYTECODE: '1' },
    windowsHide: true, stdio: ['ignore', output, output],
  })
  closeSync(output)
  owned.push(child)
  child.on('error', (error) => { child.startError = error })
  return child
}

async function waitReady(child, url, expected) {
  for (let attempt = 0; attempt < 45; attempt++) {
    if (child.startError) throw child.startError
    if (child.exitCode !== null) throw new Error(`Service stopped. See ${join(root, '.run')} for logs.`)
    if (await reachable(url, expected)) return
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`Service did not become ready: ${url}. Check .run logs.`)
}

let stopping = false
async function stop() {
  if (stopping) return
  stopping = true
  for (const child of owned) {
    if (!child.pid || child.exitCode !== null) continue
    if (process.platform === 'win32') {
      // Only stop process trees created by this invocation, never existing services.
      await new Promise((resolve) => {
        const killer = spawn('taskkill.exe', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' })
        killer.on('error', resolve)
        killer.on('exit', resolve)
      })
    } else child.kill('SIGTERM')
  }
}

for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => void stop().then(() => process.exit(0)))

try {
  if (!Number.isInteger(backendPort) || !Number.isInteger(frontendPort) || backendPort < 1 || frontendPort < 1 || backendPort > 65535 || frontendPort > 65535) throw new Error('Invalid service port')
  if (process.argv.includes('--check')) {
    const checks = await Promise.all([
      reachable(`${backendUrl}/health`, 'ok'),
      reachable(`${frontendUrl}/@vite/client`, 'vite'),
      reachable(`${frontendUrl}/api/health`, 'ok'),
    ])
    console.log(JSON.stringify({ backend: checks[0], frontend: checks[1], proxy: checks[2] }))
    if (checks.some((ok) => !ok)) process.exitCode = 1
  } else {
    if (!await reachable(`${backendUrl}/health`, 'ok')) {
      const python = join(backendRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
      if (!existsSync(python)) throw new Error('Backend .venv missing. Follow README.md to install dependencies.')
      const child = launch(python, ['-B', '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(backendPort)], backendRoot, 'backend')
      await waitReady(child, `${backendUrl}/health`, 'ok')
    }
    if (await reachable(frontendUrl)) {
      if (!await reachable(`${frontendUrl}/@vite/client`, 'vite') || !await reachable(`${frontendUrl}/api/health`, 'ok')) throw new Error(`Port ${frontendPort} belongs to another or unhealthy frontend.`)
    } else {
      const vite = join(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js')
      if (!existsSync(vite)) throw new Error('Run npm ci in culture-front/Cultural heritage first.')
      const child = launch(process.execPath, [vite, '--host', '127.0.0.1', '--port', String(frontendPort)], frontendRoot, 'frontend', { BACKEND_URL: backendUrl, VITE_API_URL: '/api' })
      await waitReady(child, `${frontendUrl}/api/health`, 'ok')
    }
    console.log(`Frontend: ${frontendUrl}\nAPI docs: ${backendUrl}/docs`)
    console.log('RAG also requires Milvus, the embedding model and available AI services. See README.md.')
    if (owned.length) {
      console.log('Keep this terminal open. Ctrl+C stops only services started here.')
      await Promise.race(owned.map((child) => new Promise((_, reject) => {
        child.on('exit', (code) => reject(new Error(`Service exited (${code}); see .run logs.`)))
      })))
    } else console.log('Existing services are ready; no new process was started.')
  }
} catch (error) {
  console.error(error.message)
  process.exitCode = 1
  await stop()
}
