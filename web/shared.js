/**
 * shared.js — 공통 상수·유틸·폴링·브로드캐스트
 * index.html, player.html, editor.html 에서 공유
 */

// ── 상태 상수 ──────────────────────────────────────────────────────────────────
const STATUS = Object.freeze({
  QUEUED:              'queued',
  TRANSCRIBING:        'transcribing',
  REFINING_KO:         'refining_ko',
  REVIEWING_KO:        'reviewing_ko',
  READY_TO_REVIEW_KO:  'ready_to_review_ko',
  TRANSLATING:         'translating',
  REVIEWING:           'reviewing',
  READY_TO_TRANSLATE:  'ready_to_translate',
  READY_TO_ENCODE:     'ready_to_encode',
  ENCODING:            'encoding',
  DONE:                'done',
  ERROR:               'error',
  CANCELLED:           'cancelled',
});

const TERMINAL = new Set([STATUS.DONE, STATUS.ERROR, STATUS.CANCELLED, STATUS.READY_TO_TRANSLATE, STATUS.READY_TO_REVIEW_KO]);

const STATUS_LABELS = Object.freeze({
  [STATUS.QUEUED]:             '대기 중...',
  [STATUS.TRANSCRIBING]:       '음성 인식 중...',
  [STATUS.REFINING_KO]:        '한국어 자막 다듬는 중...',
  [STATUS.REVIEWING_KO]:       '한국어 자막 AI 검토 중...',
  [STATUS.READY_TO_REVIEW_KO]: '한국어 자막 확인 대기',
  [STATUS.TRANSLATING]:        '번역 중...',
  [STATUS.REVIEWING]:          '번역 품질 검토 중...',
  [STATUS.READY_TO_TRANSLATE]: '번역 실패 — 재시도 가능',
  [STATUS.READY_TO_ENCODE]:    '번역 완료',
  [STATUS.ENCODING]:           '번인 인코딩 중...',
  [STATUS.DONE]:               '완료',
  [STATUS.ERROR]:              '오류',
  [STATUS.CANCELLED]:          '취소됨',
});

const STEP_MAP = Object.freeze({
  [STATUS.TRANSCRIBING]:       'step-transcribe',
  [STATUS.REFINING_KO]:        'step-translate',
  [STATUS.REVIEWING_KO]:       'step-translate',
  [STATUS.READY_TO_REVIEW_KO]: 'step-translate',
  [STATUS.TRANSLATING]:        'step-translate',
  [STATUS.REVIEWING]:          'step-review',
  [STATUS.READY_TO_TRANSLATE]: 'step-translate',
  [STATUS.READY_TO_ENCODE]:    'step-encode',
  [STATUS.ENCODING]:           'step-encode',
  [STATUS.DONE]:               'step-done',
});

const MSG = Object.freeze({
  SUBTITLE_UPDATED: 'subtitle_updated',
  RELOAD_SUBTITLE:  'reload_subtitle',
});

const UPLOAD = Object.freeze({
  MAX_MB:       4096,
  ALLOWED_EXTS: ['.mp4','.mkv','.mov','.avi','.webm','.flv','.wmv','.m4v'],
});

// ── UI 라벨 (다국어 대응 준비) ────────────────────────────────────────────────
const LABELS = Object.freeze({
  // 공통
  PROCESSING:         '처리 중...',
  // 업로드
  START:              '자막 생성 시작',
  START_WITH_SRT:     '번역 시작',
  UPLOADING:          '업로드 중...',
  NEW_FILE:           '새 파일 처리',
  UNSUPPORTED_FMT:    '지원하지 않는 형식입니다.',
  FILE_TOO_LARGE:     '파일이 너무 큽니다.',
  CLICK_TO_CHANGE:    '클릭하여 변경',
  // 취소
  CANCEL:             '취소',
  CANCELLING:         '취소 중...',
  // 인코딩
  ENCODE_START:       '번인 시작',
  ENCODING:           '번인 중...',
  ENCODE_DONE:        '자막 번인 완료',
  // 에디터
  TRANSLATE:          '번역',
  TRANSLATING:        '번역 중...',
  SAVING:             '저장 중...',
  SAVED:              '✓ 저장됨',
  SAVE_FAILED:        '저장 실패',
  LOAD_FAILED:        '불러오기 실패',
  RETRANSLATE_FAILED: '재번역 실패',
  NO_SUBTITLES:       '자막이 없습니다.',
  LOADING_SUBTITLES:  '자막을 불러오는 중...',
  INVALID_ACCESS:     '잘못된 접근입니다.',
  IMPORT_PARSE_ERROR: 'SRT 파싱 실패: 올바른 형식의 .srt 파일인지 확인해주세요.',
  RESET_CONFIRM:      '편집 내용을 되돌리시겠습니까?',
  CHAR_UNIT:          '자',
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

// ── 다운로드 헬퍼 ──────────────────────────────────────────────────────────────
function triggerDownload({ href, filename = '', revokeAfterMs = 1000 }) {
  const a = document.createElement('a');
  a.href = href;
  if (filename) a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  // Chrome 계열에서 object URL을 너무 빨리 해제하면 .crdownload 로 남을 수 있다.
  if (href.startsWith('blob:')) {
    window.setTimeout(() => URL.revokeObjectURL(href), revokeAfterMs);
  }
}

function downloadBlob(content, filename, type = 'application/octet-stream') {
  const href = URL.createObjectURL(new Blob([content], { type }));
  triggerDownload({ href, filename });
}
