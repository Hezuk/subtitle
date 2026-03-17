import torch
import torchaudio
import time

from tada.modules.encoder import Encoder
from tada.modules.tada import TadaForCausalLM

device = "cpu"  # CPU로 먼저 테스트

print(f"PyTorch: {torch.__version__}, device: {device}")
print("Loading encoder...")
t0 = time.time()

encoder = Encoder.from_pretrained("HumeAI/tada-codec", subfolder="encoder").to(device)
print(f"Encoder loaded in {time.time()-t0:.1f}s")

print("Loading model (TADA-1B)...")
t1 = time.time()
model = TadaForCausalLM.from_pretrained("HumeAI/tada-1b", torch_dtype=torch.bfloat16).to(device)
print(f"Model loaded in {time.time()-t1:.1f}s")

# Use TADA's sample reference audio
sample_wav = "/Users/sunghoonkim/claude/subtitle/tada/tada/samples/ljspeech.wav"
audio, sample_rate = torchaudio.load(sample_wav)
audio = audio.to(device)

prompt_text = "The examination and testimony of the experts."
prompt = encoder(audio, text=[prompt_text], sample_rate=sample_rate)

print("Generating speech...")
t2 = time.time()
output = model.generate(
    prompt=prompt,
    text="Hello, this is a test of the TADA text to speech system.",
)
print(f"Generated in {time.time()-t2:.1f}s")

# Save output
out_path = "/Users/sunghoonkim/claude/subtitle/tada_output.wav"
audio_tensor = output.audio[0] if isinstance(output.audio, list) else output.audio
if not isinstance(audio_tensor, torch.Tensor):
    audio_tensor = torch.tensor(audio_tensor)
if audio_tensor.dim() == 1:
    audio_tensor = audio_tensor.unsqueeze(0)
sr = 24000  # TADA default sample rate
torchaudio.save(out_path, audio_tensor.cpu().float(), sr)
print(f"Saved to {out_path}")
print(f"Duration: {audio_tensor.shape[-1] / sr:.2f}s")
