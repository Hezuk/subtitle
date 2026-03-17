#!/usr/bin/env python3
"""TADA TTS dubbing worker. Called as subprocess from main.py.

Usage: tada_env/bin/python dub_worker.py <job_id>
Stdout: PROGRESS:{pct}:{msg}  |  DONE  |  ERROR:{msg}
"""
import sys, re
import numpy as np
from pathlib import Path
import torch
import torchaudio
import warnings
warnings.filterwarnings("ignore")

from tada.modules.encoder import Encoder
from tada.modules.tada import TadaForCausalLM

SAMPLE_RATE = 24000
DEVICE = "cpu"
BASE = Path(__file__).parent


def parse_srt(content):
    blocks = []
    pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n([\s\S]*?)(?=\n\n\d+\n|\Z)'
    for m in re.finditer(pattern, content.strip()):
        blocks.append({
            'idx':  m.group(1),
            'start': ts_to_sec(m.group(2)),
            'end':   ts_to_sec(m.group(3)),
            'text':  m.group(4).strip().replace('\n', ' '),
        })
    return blocks


def ts_to_sec(ts):
    h, m, rest = ts.split(':')
    s, ms = rest.split(',')
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000


def main():
    if len(sys.argv) < 2:
        print("ERROR:job_id argument required", flush=True)
        sys.exit(1)

    job_id   = sys.argv[1]
    srt_path = BASE / 'uploads' / f'{job_id}_en.srt'
    out_path = BASE / 'uploads' / f'{job_id}_dub.wav'
    ref_wav  = BASE / 'tada' / 'tada' / 'samples' / 'ljspeech.wav'

    if not srt_path.exists():
        print(f"ERROR:SRT not found: {srt_path}", flush=True)
        sys.exit(1)

    blocks = parse_srt(srt_path.read_text(encoding='utf-8'))
    if not blocks:
        print("ERROR:No subtitle blocks found", flush=True)
        sys.exit(1)

    total = len(blocks)
    print(f"PROGRESS:0:TADA 모델 로딩 중...", flush=True)

    encoder = Encoder.from_pretrained("HumeAI/tada-codec", subfolder="encoder").to(DEVICE)
    model   = TadaForCausalLM.from_pretrained("HumeAI/tada-1b", torch_dtype=torch.bfloat16).to(DEVICE)

    # Reference audio → cached prompt
    ref_audio, ref_sr = torchaudio.load(str(ref_wav))
    prompt = encoder(ref_audio.to(DEVICE),
                     text=["The examination and testimony of the experts."],
                     sample_rate=ref_sr)

    # Silent audio timeline
    total_dur     = max(b['end'] for b in blocks) + 3.0
    total_samples = int(total_dur * SAMPLE_RATE)
    timeline      = np.zeros(total_samples, dtype=np.float32)

    print(f"PROGRESS:5:더빙 오디오 생성 중 (총 {total}개)...", flush=True)

    for i, block in enumerate(blocks):
        text = block['text']
        if not text:
            continue
        try:
            with torch.no_grad():
                output = model.generate(prompt=prompt, text=text)
            audio = output.audio[0] if isinstance(output.audio, list) else output.audio
            if not isinstance(audio, torch.Tensor):
                audio = torch.tensor(audio)
            audio_np = audio.cpu().float().numpy()

            start_s = int(block['start'] * SAMPLE_RATE)
            end_s   = min(start_s + len(audio_np), total_samples)
            timeline[start_s:end_s] += audio_np[:end_s - start_s]

        except Exception as e:
            print(f"WARNING:block {block['idx']} 실패: {e}", flush=True)

        pct = 5 + int((i + 1) / total * 90)
        print(f"PROGRESS:{pct}:{i+1}/{total} 세그먼트 완료", flush=True)

    # Normalize
    max_val = np.abs(timeline).max()
    if max_val > 0:
        timeline = timeline / max_val * 0.9

    torchaudio.save(str(out_path), torch.from_numpy(timeline).unsqueeze(0), SAMPLE_RATE)
    print("DONE", flush=True)


if __name__ == '__main__':
    main()
