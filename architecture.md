# main.py Architecture

## 1. Processing Pipeline

```mermaid
flowchart LR
    Upload["📁 Upload\nPOST /upload"]
    Whisper["🎤 Whisper\n음성인식"]
    Gemini["🌍 Gemini\n번역 + QA"]
    Wait["✏️ 편집 대기\n사용자 확인"]
    FFmpeg["🎬 ffmpeg\n자막 번인"]
    Done["✅ 완료\n다운로드"]

    Upload -->|"executor.submit\n(run_pipeline)"| Whisper
    Whisper -->|"{job_id}.srt\n한국어"| Gemini
    Gemini -->|"{job_id}_en.srt\n영어"| Wait
    Wait -->|"POST /encode\n(run_encode)"| FFmpeg
    FFmpeg -->|"outputs/{job_id}.mp4"| Done

    style Upload fill:#1a1d27,stroke:#38bdf8,color:#e2e8f0
    style Whisper fill:#1a1d27,stroke:#818cf8,color:#e2e8f0
    style Gemini fill:#1a1d27,stroke:#818cf8,color:#e2e8f0
    style Wait fill:#1a1d27,stroke:#fbbf24,color:#e2e8f0
    style FFmpeg fill:#1a1d27,stroke:#818cf8,color:#e2e8f0
    style Done fill:#1a1d27,stroke:#34d399,color:#e2e8f0
```

## 2. Job State Machine

```mermaid
stateDiagram-v2
    [*] --> queued: POST /upload
    queued --> transcribing: run_pipeline()
    transcribing --> translating: Whisper 완료
    translating --> ready_to_encode: 번역+QA 완료
    ready_to_encode --> encoding: POST /encode
    encoding --> done: ffmpeg 완료
    transcribing --> error: 예외 발생
    translating --> error: 예외 발생
    encoding --> error: 예외 발생
    done --> [*]
    error --> [*]
```

## 3. Gemini QA Review Loop

```mermaid
flowchart TB
    Start["한국어 블록"] --> Translate["translate_with_gemini()\n전체 블록 한→영 번역"]
    Translate --> Review["review_with_gemini()\n미번역·비문·일관성 검토"]
    Review --> Judge{판정}
    Judge -->|"모두 통과"| Save["en.srt 저장\nready_to_encode"]
    Judge -->|"[RETRANSLATE] 있음"| Retry["불량 블록만\ntranslate_with_gemini()"]
    Retry -->|"MAX_RETRIES=2\n이내"| Review
    Retry -->|"최대 횟수 초과"| Save

    style Start fill:#1a1d27,stroke:#fbbf24,color:#e2e8f0
    style Translate fill:#1a1d27,stroke:#818cf8,color:#e2e8f0
    style Review fill:#1a1d27,stroke:#38bdf8,color:#e2e8f0
    style Judge fill:#1a1d27,stroke:#f472b6,color:#e2e8f0
    style Save fill:#1a1d27,stroke:#34d399,color:#e2e8f0
    style Retry fill:#1a1d27,stroke:#fbbf24,color:#e2e8f0
```

## 4. API Endpoints

```mermaid
flowchart LR
    subgraph Pages["정적 페이지"]
        P1["GET /"]
        P2["GET /player"]
        P3["GET /editor"]
    end

    subgraph Core["핵심 흐름"]
        C1["POST /upload"]
        C2["GET /status/{id}"]
        C3["GET /original/{id}"]
        C4["POST /encode/{id}"]
        C5["GET /download/{id}"]
    end

    subgraph Subtitle["자막 API"]
        S1["GET /subtitle/{id}\nWebVTT (en/ko/both)"]
        S2["GET /subtitles/{id}\nJSON CRUD"]
        S3["POST /retranslate/{id}\n개별 재번역"]
    end

    style Pages fill:#13151e,stroke:#2d3148,color:#94a3b8
    style Core fill:#13151e,stroke:#818cf8,color:#94a3b8
    style Subtitle fill:#13151e,stroke:#38bdf8,color:#94a3b8
```

## 5. File Lifecycle

```mermaid
flowchart TB
    subgraph uploads["uploads/"]
        U1["🎥 {id}.mp4\n원본 영상"]
        U2["📄 {id}.srt\n한국어 자막"]
        U3["📝 {id}_en.srt\n영어 자막"]
    end

    subgraph outputs["outputs/"]
        O1["✅ {id}.mp4\n완성 영상"]
    end

    U1 -->|"done 후 삭제"| DEL["🗑️"]
    U2 -->|"done 후 삭제"| DEL
    U3 -->|"done 후 삭제"| DEL
    O1 -->|"영구 보관"| KEEP["💾"]

    style uploads fill:#1a1d27,stroke:#fbbf24,color:#e2e8f0
    style outputs fill:#1a1d27,stroke:#34d399,color:#e2e8f0
    style DEL fill:#1a1d27,stroke:#f87171,color:#f87171
    style KEEP fill:#1a1d27,stroke:#34d399,color:#34d399
```

## 6. Frontend 통신 구조

```mermaid
sequenceDiagram
    participant I as index.html
    participant S as Server (main.py)
    participant P as player.html
    participant E as editor.html

    I->>S: POST /upload (파일)
    S-->>I: {job_id}

    loop 2초마다 폴링
        I->>S: GET /status/{id}
        S-->>I: {status, message, progress, elapsed}
    end

    Note over I,P: status = ready_to_encode
    I->>P: window.open(/player?job_id=xxx)
    P->>S: GET /status/{id}
    P->>S: GET /original/{id}
    S-->>P: 원본 영상 스트림

    I->>E: window.open(/editor?job_id=xxx)
    E->>S: GET /subtitles/{id}
    E->>S: POST /subtitles/{id} (저장)
    E->>I: postMessage(subtitle_updated)
    I->>P: postMessage(reload_subtitle)

    Note over I,S: 사용자가 번인 클릭
    I->>S: POST /encode/{id}

    loop 폴링 계속
        I->>S: GET /status/{id}
    end

    Note over I,P: status = done
    I->>P: window.open(/player?job_id=xxx)
    P->>S: GET /download/{id}
    S-->>P: 완성 영상 스트림
    I->>S: GET /download/{id} (다운로드)
```

## 7. Internal Functions Map

```mermaid
flowchart TB
    subgraph Pipeline["run_pipeline() — Phase 1"]
        direction TB
        WM["whisper_model.transcribe()"] --> STS["segments_to_srt()"]
        STS --> PS["parse_srt()"]
        PS --> TWG["translate_with_gemini()"]
        TWG --> RWG["review_with_gemini()"]
        RWG --> WS["wrap_subtitle()"]
    end

    subgraph Encode["run_encode() — Phase 2"]
        direction TB
        FF["subprocess: ffmpeg\nlibx264 + libass\n-movflags +faststart"]
    end

    subgraph Helpers["Helpers"]
        direction TB
        FE["fmt_elapsed()\n초 → '1분 23초'"]
        STT["seconds_to_srt_time()\n초 → HH:MM:SS,mmm"]
        STV["srt_to_vtt()\nSRT → WebVTT"]
        LB["_load_blocks() / _save_blocks()\nSRT 파일 CRUD"]
    end

    subgraph Retranslate["retranslate_with_gemini()"]
        direction TB
        RT["에디터 단건 재번역\nPOST /retranslate"]
    end

    Pipeline -->|"ready_to_encode"| Encode
    Pipeline -.->|"재번역 요청"| Retranslate

    style Pipeline fill:#13151e,stroke:#818cf8,color:#94a3b8
    style Encode fill:#13151e,stroke:#34d399,color:#94a3b8
    style Helpers fill:#13151e,stroke:#2d3148,color:#94a3b8
    style Retranslate fill:#13151e,stroke:#38bdf8,color:#94a3b8
```
