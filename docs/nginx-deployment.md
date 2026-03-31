# Nginx deployment for `hezuk.com`

이 프로젝트는 FastAPI 앱을 `127.0.0.1:8000`에서 띄우고, nginx가 `hezuk.com`과 `www.hezuk.com` 요청을 받아 프록시하는 방식으로 배포하면 됩니다.

## 1. DNS 연결

도메인 구매처에서 아래처럼 설정합니다.

- `A` 레코드: `@` -> 서버 공인 IP
- `A` 레코드: `www` -> 서버 공인 IP

DNS 반영 전에는 nginx를 설정해도 브라우저에서 접속되지 않을 수 있습니다.

## 2. 앱 서버 준비

서버에서 프로젝트를 원하는 위치에 두고 `.env`를 준비합니다.

```bash
cd /Users/sunghoonkim/claude/subtitle
cp .env.example .env
```

`.env`에서 최소한 `GEMINI_API_KEY`를 채워야 합니다.

필수 패키지 예시:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn aiofiles python-multipart openai-whisper requests python-dotenv
```

`ffmpeg`도 설치되어 있어야 합니다.

## 3. systemd 서비스 등록

배포 서버가 Linux라면 [`deploy/systemd/subtitle.service`](/Users/sunghoonkim/claude/subtitle/deploy/systemd/subtitle.service) 파일을 사용하면 됩니다.

주의:

- `User`
- `Group`
- `WorkingDirectory`

위 3개 값은 실제 서버 환경에 맞게 바꿔야 합니다.

예시 명령:

```bash
sudo cp deploy/systemd/subtitle.service /etc/systemd/system/subtitle.service
sudo systemctl daemon-reload
sudo systemctl enable subtitle
sudo systemctl start subtitle
sudo systemctl status subtitle
```

## 4. nginx 연결

[`deploy/nginx/hezuk.com.conf`](/Users/sunghoonkim/claude/subtitle/deploy/nginx/hezuk.com.conf) 파일을 `/etc/nginx/sites-available/hezuk.com` 등에 복사해 사용합니다.

```bash
sudo cp deploy/nginx/hezuk.com.conf /etc/nginx/sites-available/hezuk.com
sudo ln -s /etc/nginx/sites-available/hezuk.com /etc/nginx/sites-enabled/hezuk.com
sudo nginx -t
sudo systemctl reload nginx
```

이 설정은 아래를 포함합니다.

- `hezuk.com` -> `www.hezuk.com` 리다이렉트
- `server_name www.hezuk.com`
- 대용량 업로드 대응용 `client_max_body_size 4096M`
- FastAPI로 프록시하는 `proxy_pass http://127.0.0.1:8000`
- 긴 변환 작업을 위한 긴 타임아웃

## 5. HTTPS 붙이기

도메인이 열리면 certbot으로 TLS를 붙이는 것이 좋습니다.

Ubuntu 기준 예시:

```bash
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d hezuk.com -d www.hezuk.com
```

certbot이 성공하면 nginx 설정에 HTTPS 서버 블록이 자동 추가되거나 수정됩니다.

## 6. 점검 순서

앱만 먼저 확인:

```bash
curl http://127.0.0.1:8000/status/test
```

nginx 확인:

```bash
curl -I http://hezuk.com
curl -I http://www.hezuk.com
```

## 7. 흔한 문제

- 413 에러: `client_max_body_size`가 업로드 파일보다 작습니다.
- 502 에러: `uvicorn` 서비스가 죽었거나 포트가 다릅니다.
- 자막 번인 중 끊김: nginx 타임아웃이 너무 짧을 수 있습니다.
- HTTPS 발급 실패: DNS가 아직 전파되지 않았거나 80 포트가 막혀 있을 수 있습니다.
