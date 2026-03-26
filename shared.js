/**
 * shared.js — 공통 상수·유틸·폴링·브로드캐스트
 * index.html, player.html, editor.html 에서 공유
 */

// ── 상태 상수 ──────────────────────────────────────────────────────────────────
const STATUS = Object.freeze({
  QUEUED:           'queued',
  TRANSCRIBING:     'transcribing',
  TRANSLATING:      'translating',
  REVIEWING:        'reviewing',
  READY_TO_ENCODE:  'ready_to_encode',
  ENCODING:         'encoding',
  DONE:             'done',
  ERROR:            'error',
  CANCELLED:        'cancelled',
});

const TERMINAL = new Set([STATUS.DONE, STATUS.ERROR, STATUS.CANCELLED]);

const STATUS_LABELS = Object.freeze({
  [STATUS.QUEUED]:          '대기 중...',
  [STATUS.TRANSCRIBING]:    '🎤 음성 인식 중...',
  [STATUS.TRANSLATING]:     '🌍 번역 중...',
  [STATUS.REVIEWING]:       '🔍 번역 품질 검토 중...',
  [STATUS.READY_TO_ENCODE]: '✅ 번역 완료',
  [STATUS.ENCODING]:        '🎬 번인 인코딩 중...',
  [STATUS.DONE]:            '✅ 완료',
  [STATUS.ERROR]:           '❌ 오류',
  [STATUS.CANCELLED]:       '🚫 취소됨',
});

const STEP_MAP = Object.freeze({
  [STATUS.TRANSCRIBING]:    'step-transcribe',
  [STATUS.TRANSLATING]:     'step-translate',
  [STATUS.REVIEWING]:       'step-review',
  [STATUS.READY_TO_ENCODE]: 'step-encode',
  [STATUS.ENCODING]:        'step-encode',
  [STATUS.DONE]:            'step-done',
});

const MSG = Object.freeze({
  SUBTITLE_UPDATED: 'subtitle_updated',
  RELOAD_SUBTITLE:  'reload_subtitle',
});

const UPLOAD = Object.freeze({
  MAX_MB:       4096,
  ALLOWED_EXTS: ['.mp4','.mkv','.mov','.avi','.webm','.flv','.wmv','.m4v'],
});

// ── URL 파라미터 ──────────────────────────────────────────────────────────────
function getJobId() {
  return new URLSearchParams(location.search).get('job_id');
}

// ── API fetch 래퍼 ────────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || data.error || 'Request failed');
  return data;
}

async function apiPost(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ── 상태 폴링 ─────────────────────────────────────────────────────────────────
function createPoller(jobId, onUpdate, interval = 2000) {
  let timer = null;

  function poll() {
    timer = setTimeout(async () => {
      try {
        const data = await api(`/status/${jobId}`);
        onUpdate(data);
        if (!TERMINAL.has(data.status)) poll();
      } catch {
        poll();
      }
    }, interval);
  }

  return {
    start: poll,
    stop() { clearTimeout(timer); timer = null; },
  };
}

// ── 상태 메시지 포맷 ──────────────────────────────────────────────────────────
function fmtStatus(data) {
  const msg = data.message || STATUS_LABELS[data.status] || data.status;
  const elapsed = data.elapsed ? ` (${data.elapsed} 경과)` : '';
  return msg + elapsed;
}

// ── BroadcastChannel ──────────────────────────────────────────────────────────
function onSubtitleBroadcast(jobId, callback) {
  try {
    const bc = new BroadcastChannel(`subtitle-${jobId}`);
    bc.onmessage = callback;
    return bc;
  } catch { return null; }
}

function broadcastSubtitleUpdated(jobId) {
  try { new BroadcastChannel(`subtitle-${jobId}`).postMessage(MSG.SUBTITLE_UPDATED); } catch {}
}
