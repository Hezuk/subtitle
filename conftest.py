import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가하여 모듈 import 가능하게
sys.path.insert(0, str(Path(__file__).parent))
