#!/usr/bin/env node
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const SCRIPT_PATH = resolve('backend/scripts/prelabel_ball_frames.py');
const MIN_MAJOR = 3;
const MIN_MINOR = 10;

function candidateCommands() {
  const candidates = [];
  if (process.env.PYTHON) {
    candidates.push({ command: process.env.PYTHON, prefixArgs: [] });
  }

  const windowsVenv = resolve('backend/.venv/Scripts/python.exe');
  const posixVenv = resolve('backend/.venv/bin/python');
  if (existsSync(windowsVenv)) {
    candidates.push({ command: windowsVenv, prefixArgs: [] });
  }
  if (existsSync(posixVenv)) {
    candidates.push({ command: posixVenv, prefixArgs: [] });
  }

  candidates.push(
    { command: 'python3', prefixArgs: [] },
    { command: 'py', prefixArgs: ['-3'] },
    { command: 'python', prefixArgs: [] },
  );
  return candidates;
}

function versionFor(candidate) {
  const result = spawnSync(
    candidate.command,
    [
      ...candidate.prefixArgs,
      '-c',
      'import sys; print("%s.%s.%s" % (sys.version_info[0], sys.version_info[1], sys.version_info[2]))',
    ],
    { encoding: 'utf8' },
  );
  if (result.error || result.status !== 0) {
    return null;
  }
  const text = String(result.stdout || result.stderr || '').trim();
  const match = text.match(/(\d+)\.(\d+)\.(\d+)/);
  if (!match) {
    return null;
  }
  return {
    raw: match[0],
    major: Number(match[1]),
    minor: Number(match[2]),
  };
}

function findPython() {
  for (const candidate of candidateCommands()) {
    const version = versionFor(candidate);
    if (!version) {
      continue;
    }
    if (version.major > MIN_MAJOR || (version.major === MIN_MAJOR && version.minor >= MIN_MINOR)) {
      return { ...candidate, version };
    }
  }
  return null;
}

const python = findPython();
if (!python) {
  console.error(
    `Could not find Python ${MIN_MAJOR}.${MIN_MINOR}+ for ball pre-labeling. ` +
      'Create backend/.venv, set PYTHON to a Python 3 executable, or install python3/py launcher.',
  );
  process.exit(1);
}

const result = spawnSync(
  python.command,
  [...python.prefixArgs, SCRIPT_PATH, ...process.argv.slice(2)],
  {
    cwd: resolve('.'),
    encoding: 'utf8',
    stdio: 'inherit',
  },
);

if (result.error) {
  console.error(`Failed to start ${python.command}: ${result.error.message}`);
  process.exit(1);
}

process.exit(result.status ?? 1);
