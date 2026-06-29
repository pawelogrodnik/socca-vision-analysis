#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import {
  basename,
  extname,
  isAbsolute,
  join,
  resolve,
} from 'node:path';

const DEFAULT_OUTPUT_ROOT = 'training_frames';
const DEFAULT_FRAME_COUNT = 100;
const DEFAULT_JPEG_QUALITY = 2;
const FFMPEG_BIN = process.env.FFMPEG_PATH || 'ffmpeg';
const FFPROBE_BIN = process.env.FFPROBE_PATH || 'ffprobe';

function printUsage() {
  console.log(`
Extract evenly sampled JPG frames from a video for annotation/training.

Usage:
  npm run extract:frames -- --video "C:\\path\\to\\video.mp4" --frames 100

Options:
  --video, -v      Path to input video. Required.
  --frames, -n     Number of JPG frames to export. Default: ${DEFAULT_FRAME_COUNT}.
  --out, -o        Output root directory. Default: ${DEFAULT_OUTPUT_ROOT}.
  --quality, -q    ffmpeg JPG quality, 2 is high quality. Default: ${DEFAULT_JPEG_QUALITY}.
  --overwrite      Replace output directory if it already exists.
  --help, -h       Show this help.

Environment:
  FFMPEG_PATH      Optional absolute path to ffmpeg binary.
  FFPROBE_PATH     Optional absolute path to ffprobe binary.

Output:
  ${DEFAULT_OUTPUT_ROOT}/<video-name>_<count>frames/
    frame_000001.jpg
    frame_000002.jpg
    metadata.json
`);
}

function parseArgs(argv) {
  const parsed = {
    positional: [],
    help: false,
    overwrite: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--help' || arg === '-h') {
      parsed.help = true;
    } else if (arg === '--overwrite') {
      parsed.overwrite = true;
    } else if (arg === '--video' || arg === '-v') {
      parsed.video = nextValue(argv, index, arg);
      index += 1;
    } else if (arg === '--frames' || arg === '-n') {
      parsed.frames = nextValue(argv, index, arg);
      index += 1;
    } else if (arg === '--out' || arg === '-o') {
      parsed.out = nextValue(argv, index, arg);
      index += 1;
    } else if (arg === '--quality' || arg === '-q') {
      parsed.quality = nextValue(argv, index, arg);
      index += 1;
    } else if (arg.startsWith('-')) {
      throw new Error(`Unknown option: ${arg}`);
    } else {
      parsed.positional.push(arg);
    }
  }

  if (!parsed.video && parsed.positional.length > 0) {
    parsed.video = parsed.positional[0];
  }

  return parsed;
}

function nextValue(argv, index, optionName) {
  const value = argv[index + 1];
  if (!value || value.startsWith('-')) {
    throw new Error(`Missing value for ${optionName}`);
  }
  return value;
}

function positiveInteger(value, fallback, label) {
  if (value === undefined) {
    return fallback;
  }
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${label} must be a positive integer.`);
  }
  return parsed;
}

function parseRate(rate) {
  if (!rate || rate === '0/0') {
    return null;
  }
  if (rate.includes('/')) {
    const [numerator, denominator] = rate.split('/').map(Number);
    if (Number.isFinite(numerator) && Number.isFinite(denominator) && denominator !== 0) {
      return numerator / denominator;
    }
    return null;
  }
  const parsed = Number(rate);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    maxBuffer: 1024 * 1024 * 32,
    ...options,
  });
  if (result.error) {
    throw new Error(
      `${command} failed to start. Make sure ffmpeg/ffprobe is installed and available in PATH ` +
        `or set FFMPEG_PATH/FFPROBE_PATH. ` +
        `Original error: ${result.error.message}`,
    );
  }
  if (result.status !== 0) {
    const output = [result.stderr, result.stdout].filter(Boolean).join('\n').trim();
    throw new Error(`${command} exited with code ${result.status}.\n${output}`);
  }
  return result;
}

function probeVideo(videoPath) {
  const fastProbe = ffprobe(videoPath, false);
  const stream = fastProbe.streams?.[0] || {};
  const format = fastProbe.format || {};
  const fps = parseRate(stream.avg_frame_rate) || parseRate(stream.r_frame_rate);
  const durationSec = numeric(stream.duration) || numeric(format.duration);
  let totalFrames = integer(stream.nb_frames);

  if (!totalFrames && fps && durationSec) {
    totalFrames = Math.round(fps * durationSec);
  }

  if (!totalFrames) {
    const countedProbe = ffprobe(videoPath, true);
    const countedStream = countedProbe.streams?.[0] || {};
    totalFrames = integer(countedStream.nb_read_frames);
  }

  if (!fps) {
    throw new Error('Could not determine video FPS with ffprobe.');
  }
  if (!durationSec) {
    throw new Error('Could not determine video duration with ffprobe.');
  }
  if (!totalFrames) {
    throw new Error('Could not determine total frame count with ffprobe.');
  }

  return {
    durationSec,
    fps,
    totalFrames,
    width: integer(stream.width),
    height: integer(stream.height),
  };
}

function ffprobe(videoPath, countFrames) {
  const entries = countFrames
    ? 'stream=nb_read_frames,r_frame_rate,avg_frame_rate,duration,width,height:format=duration'
    : 'stream=nb_frames,r_frame_rate,avg_frame_rate,duration,width,height:format=duration';
  const args = [
    '-v',
    'error',
    ...(countFrames ? ['-count_frames'] : []),
    '-select_streams',
    'v:0',
    '-show_entries',
    entries,
    '-of',
    'json',
    videoPath,
  ];
  const result = run(FFPROBE_BIN, args);
  return JSON.parse(result.stdout);
}

function numeric(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function integer(value) {
  const parsed = Number.parseInt(String(value || ''), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function sanitizeName(name) {
  const withoutExt = name.slice(0, name.length - extname(name).length) || name;
  const slug = withoutExt
    .normalize('NFKD')
    .replace(/[^\w.-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 90);
  return slug || 'video';
}

function createOutputDir(outputRoot, videoPath, requestedFrames, overwrite) {
  const root = resolve(outputRoot);
  mkdirSync(root, { recursive: true });

  const baseName = `${sanitizeName(basename(videoPath))}_${requestedFrames}frames`;
  const firstCandidate = join(root, baseName);

  if (overwrite) {
    rmSync(firstCandidate, { recursive: true, force: true });
    mkdirSync(firstCandidate, { recursive: true });
    return firstCandidate;
  }

  if (!existsSync(firstCandidate)) {
    mkdirSync(firstCandidate, { recursive: true });
    return firstCandidate;
  }

  for (let index = 1; index < 1000; index += 1) {
    const candidate = join(root, `${baseName}_${String(index).padStart(3, '0')}`);
    if (!existsSync(candidate)) {
      mkdirSync(candidate, { recursive: true });
      return candidate;
    }
  }

  throw new Error(`Could not create a unique output directory under ${root}.`);
}

function ensureVideoFile(videoPath) {
  const resolved = isAbsolute(videoPath) ? videoPath : resolve(videoPath);
  if (!existsSync(resolved)) {
    throw new Error(`Video file does not exist: ${resolved}`);
  }
  const stats = statSync(resolved);
  if (!stats.isFile()) {
    throw new Error(`Video path is not a file: ${resolved}`);
  }
  return resolved;
}

function extractFrames(videoPath, outputDir, requestedFrames, stride, quality) {
  const outputPattern = join(outputDir, 'frame_%06d.jpg');
  const filter = `select=not(mod(n\\,${stride}))`;
  const args = [
    '-hide_banner',
    '-y',
    '-i',
    videoPath,
    '-vf',
    filter,
    '-frames:v',
    String(requestedFrames),
    '-vsync',
    'vfr',
    '-q:v',
    String(quality),
    outputPattern,
  ];

  run(FFMPEG_BIN, args, { stdio: ['ignore', 'pipe', 'pipe'] });
  return {
    args,
    outputPattern,
  };
}

function countOutputFrames(outputDir) {
  return readdirSync(outputDir).filter((name) => /^frame_\d{6}\.jpg$/i.test(name)).length;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printUsage();
    return;
  }

  if (!args.video) {
    printUsage();
    throw new Error('Missing required --video path.');
  }

  const requestedFrames = positiveInteger(args.frames, DEFAULT_FRAME_COUNT, '--frames');
  const quality = positiveInteger(args.quality, DEFAULT_JPEG_QUALITY, '--quality');
  const videoPath = ensureVideoFile(args.video);
  const outputRoot = args.out || DEFAULT_OUTPUT_ROOT;

  const probe = probeVideo(videoPath);
  const stride = Math.max(1, Math.floor(probe.totalFrames / requestedFrames));
  const outputDir = createOutputDir(outputRoot, videoPath, requestedFrames, Boolean(args.overwrite));
  const extraction = extractFrames(videoPath, outputDir, requestedFrames, stride, quality);
  const outputFrames = countOutputFrames(outputDir);

  const metadata = {
    schema_version: 1,
    created_at: new Date().toISOString(),
    source_video: videoPath,
    source_video_name: basename(videoPath),
    output_dir: outputDir,
    requested_frames: requestedFrames,
    output_frames: outputFrames,
    total_frames: probe.totalFrames,
    duration_sec: Number(probe.durationSec.toFixed(3)),
    fps: Number(probe.fps.toFixed(3)),
    width: probe.width,
    height: probe.height,
    frame_stride: stride,
    approximate_source_frame_step: stride,
    sampling: {
      method: 'even_frame_stride',
      filter: `select=not(mod(n\\,${stride}))`,
      max_output_frames: requestedFrames,
      note:
        'The approximate source frame for frame_000001.jpg is 0; each following image advances by frame_stride source frames.',
    },
    jpeg_quality: quality,
    ffmpeg: {
      command: FFMPEG_BIN,
      args: extraction.args,
    },
    ffprobe: {
      command: FFPROBE_BIN,
    },
  };

  writeFileSync(join(outputDir, 'metadata.json'), `${JSON.stringify(metadata, null, 2)}\n`, 'utf8');

  console.log(`Video: ${videoPath}`);
  console.log(`Duration: ${metadata.duration_sec}s, frames: ${probe.totalFrames}, fps: ${metadata.fps}`);
  console.log(`Requested JPGs: ${requestedFrames}, frame stride: ${stride}, exported: ${outputFrames}`);
  console.log(`Output: ${outputDir}`);
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
