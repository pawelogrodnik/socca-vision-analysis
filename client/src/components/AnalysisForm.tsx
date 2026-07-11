import type { AnalysisPayload, RuntimeInfo } from '../types';

interface AnalysisFormProps {
  analysis: AnalysisPayload;
  onChange: (payload: AnalysisPayload) => void;
  runtimeInfo?: RuntimeInfo | null;
  onRun: () => Promise<void> | void;
  disabled?: boolean;
  isRunning?: boolean;
  showRunButton?: boolean;
}

function yesNo(value: boolean | undefined): string {
  return value ? 'tak' : 'nie';
}

function gpuMemoryLabel(runtimeInfo: RuntimeInfo): string | null {
  const firstTotal = runtimeInfo.torch.gpu_memory_total_mb?.[0];
  if (!firstTotal) return null;
  return `${Math.round(firstTotal)} MB`;
}

export function AnalysisForm({
  analysis,
  onChange,
  runtimeInfo = null,
  onRun,
  disabled = false,
  isRunning = false,
  showRunButton = true,
}: AnalysisFormProps) {
  return (
    <div className='analysis-form'>
      <h3>Ustawienia YOLO</h3>
      <div className='grid three compact'>
        <label>
          Adapter
          <select
            value={analysis.adapter}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                ...analysis,
                adapter: event.target.value as AnalysisPayload['adapter'],
              })
            }
          >
            <option value='yolo'>yolo</option>
            <option value='motion'>motion</option>
          </select>
        </label>
        <label>
          Max seconds
          <input
            type='number'
            value={analysis.max_seconds}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, max_seconds: Number(event.target.value) })
            }
          />
        </label>
        <label>
          Frame stride
          <input
            type='number'
            value={analysis.frame_stride}
            min={1}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                ...analysis,
                frame_stride: Number(event.target.value),
              })
            }
          />
        </label>
      </div>
      <div className='grid three compact'>
        <label>
          Model
          <input
            value={analysis.yolo_model}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_model: event.target.value })
            }
          />
        </label>
        <label>
          Conf
          <input
            type='number'
            step='0.01'
            value={analysis.yolo_conf}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_conf: Number(event.target.value) })
            }
          />
        </label>
        <label>
          Img size
          <input
            type='number'
            value={analysis.yolo_imgsz}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_imgsz: Number(event.target.value) })
            }
          />
        </label>
      </div>
      <div className='grid two compact'>
        <label>
          Tracker
          <input
            value={analysis.yolo_tracker}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_tracker: event.target.value })
            }
          />
        </label>
        <label>
          Device
          <select
            value={analysis.yolo_device || ''}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_device: event.target.value || null })
            }
          >
            <option value=''>Auto</option>
            <option value='cpu'>CPU</option>
            <option value='0'>CUDA / NVIDIA GPU 0</option>
            <option value='mps'>Apple MPS</option>
          </select>
        </label>
      </div>
      {runtimeInfo && (
        <div className='runtime-box'>
          <strong>Backend runtime</strong>
          <div className='chips'>
            <span>Python {runtimeInfo.python.version}</span>
            <span>Torch {runtimeInfo.torch.available ? runtimeInfo.torch.version || 'ok' : 'brak'}</span>
            <span>CUDA: {yesNo(runtimeInfo.torch.cuda_available)}</span>
            <span>MPS: {yesNo(runtimeInfo.torch.mps_available)}</span>
            {runtimeInfo.torch.cuda_version && <span>Torch CUDA {runtimeInfo.torch.cuda_version}</span>}
            {runtimeInfo.torch.active_cuda_device_name && (
              <span>GPU: {runtimeInfo.torch.active_cuda_device_name}</span>
            )}
            {gpuMemoryLabel(runtimeInfo) && <span>GPU RAM: {gpuMemoryLabel(runtimeInfo)}</span>}
          </div>
          {runtimeInfo.recommended_yolo_devices.length > 0 && (
            <p className='muted'>
              Suggested device: {runtimeInfo.recommended_yolo_devices.join(' / ')}
            </p>
          )}
        </div>
      )}
      <div className='artifact-box'>
        <label className='checkbox-row'>
          <input
            type='checkbox'
            checked={analysis.camera_motion_compensation}
            disabled={disabled || analysis.adapter !== 'yolo'}
            onChange={(event) =>
              onChange({ ...analysis, camera_motion_compensation: event.target.checked })
            }
          />
          Kompensuj lekkie bujanie kamery
        </label>
        <div className='grid two compact'>
          <label>
            Camera interval sec
            <input
              type='number'
              min={0.1}
              step={0.1}
              value={analysis.camera_motion_interval_sec}
              disabled={disabled || !analysis.camera_motion_compensation}
              onChange={(event) =>
                onChange({ ...analysis, camera_motion_interval_sec: Number(event.target.value) })
              }
            />
          </label>
          <label>
            Min inlier ratio
            <input
              type='number'
              min={0}
              max={1}
              step={0.05}
              value={analysis.camera_motion_min_inlier_ratio}
              disabled={disabled || !analysis.camera_motion_compensation}
              onChange={(event) =>
                onChange({ ...analysis, camera_motion_min_inlier_ratio: Number(event.target.value) })
              }
            />
          </label>
        </div>
        <p className='muted'>
          Manualna kalibracja boiska zostaje punktem odniesienia, a detekcje sa
          mapowane do tej klatki przed filtrem ROI i liczeniem metrow.
        </p>
      </div>
      <div className='artifact-box'>
        <label className='checkbox-row'>
          <input
            type='checkbox'
            checked={analysis.chunked}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, chunked: event.target.checked })
            }
          />
          Long video mode: zapisz chunk manifest
        </label>
        <label className='checkbox-row'>
          <input
            type='checkbox'
            checked={analysis.include_ball}
            disabled={disabled || analysis.adapter !== 'yolo'}
            onChange={(event) =>
              onChange({ ...analysis, include_ball: event.target.checked })
            }
          />
          Analizuj pilke w tym samym jobie
        </label>
        <label className='checkbox-row'>
          <input
            type='checkbox'
            checked={analysis.render_stable_overlay}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, render_stable_overlay: event.target.checked })
            }
          />
          Generuj stable overlay video
        </label>
        <div className='grid two compact'>
          <label>
            Chunk duration sec
            <input
              type='number'
              min={1}
              value={analysis.chunk_duration_sec}
              disabled={disabled || !analysis.chunked}
              onChange={(event) =>
                onChange({
                  ...analysis,
                  chunk_duration_sec: Number(event.target.value),
                })
              }
            />
          </label>
          <label>
            Chunk overlap sec
            <input
              type='number'
              min={0}
              value={analysis.chunk_overlap_sec}
              disabled={disabled || !analysis.chunked}
              onChange={(event) =>
                onChange({
                  ...analysis,
                  chunk_overlap_sec: Number(event.target.value),
                })
              }
            />
          </label>
        </div>
        {analysis.include_ball && (
          <div className='grid three compact'>
            <label>
              Ball model
              <input
                value={analysis.ball_yolo_model}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...analysis, ball_yolo_model: event.target.value })
                }
              />
            </label>
            <label>
              Ball conf
              <input
                type='number'
                step='0.01'
                value={analysis.ball_yolo_conf}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...analysis, ball_yolo_conf: Number(event.target.value) })
                }
              />
            </label>
            <label>
              Ball img size
              <input
                type='number'
                value={analysis.ball_yolo_imgsz}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...analysis, ball_yolo_imgsz: Number(event.target.value) })
                }
              />
            </label>
          </div>
        )}
        <p className='muted'>
          Chunked mode analizuje zakresy osobno, zapisuje status kazdego
          chunka i przy ponownym uruchomieniu pomija juz ukonczone chunki.
          Opcja pilki dodaje drugi model YOLO w tym samym jobie. Stable overlay
          jest potrzebny do wizualnej walidacji, ale mozna go pominac przy
          dlugich runach i pracowac na JSON-ach oraz cropach identity review.
        </p>
      </div>
      {showRunButton && (
        <button type='button' onClick={onRun} disabled={disabled}>
          {isRunning ? 'Analiza w toku...' : 'Uruchom analize'}
        </button>
      )}
    </div>
  );
}
