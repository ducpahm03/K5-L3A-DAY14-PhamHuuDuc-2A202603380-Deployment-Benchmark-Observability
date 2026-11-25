# Day 14 Lab — Mission Answers

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

1. **Hardcode secrets trong source code** (dòng 17–18): `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` và `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"`. Push lên GitHub public là lộ credential ngay lập tức và nằm vĩnh viễn trong lịch sử git. `production/config.py` sửa bằng `os.getenv(...)` + `.env.example` (không commit `.env`) và fail-fast validation khi thiếu `AGENT_API_KEY` ở `ENVIRONMENT=production` (dòng 42–43).
2. **In secret ra log bằng `print()`** (dòng 33–38): dùng `print()` thiếu timestamp/level và dòng 34 còn `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` làm rò rỉ API key vào log. `production/app.py` thay bằng `logging` định dạng JSON (dòng 28–32, 111–122) và chỉ log `question_length`, `client_ip`, tuyệt đối không log secret.
3. **Config cố định trong code (`DEBUG = True`, `MAX_TOKENS = 500`)** (dòng 21–22): không đổi được giữa dev/staging/production nếu không sửa code và build lại. `production/config.py` gom vào dataclass `Settings` đọc từ biến môi trường.
4. **Không có health check / readiness endpoint** (dòng 42–43): platform (Docker, Railway, Render, Kubernetes) không biết process còn sống hay đã sẵn sàng nhận traffic. `production/app.py` bổ sung `GET /health` (liveness, dòng 135–149) và `GET /ready` (readiness, trả 503 nếu chưa ready, dòng 152–161).
5. **Bind cứng `host="localhost"`, `port=8000`, `reload=True`** (dòng 49–54): `localhost` không nhận kết nối từ ngoài container; port cứng không khớp biến `PORT` do cloud inject; `reload=True` tốn tài nguyên và không ổn định trên production. `production/app.py` dùng `host=settings.host` (`0.0.0.0`), `port=settings.port` (đọc `PORT`), và chỉ bật `reload=settings.debug` (dòng 194–199).
6. **Không có graceful shutdown / lifecycle management** (dòng 47–54): container bị tắt sẽ cắt ngang request đang xử lý. `production/app.py` dùng `lifespan` (dòng 39–68) chuyển `is_ready = False` trước khi shutdown.
7. **Không có CORS và validation body JSON** (dòng 30–31): nhận `question` qua query param thay vì JSON body và không giới hạn origins. `production/app.py` thêm `CORSMiddleware` (dòng 77–82) và kiểm tra rỗng trả `422` (dòng 107–108).

### Exercise 1.2: Kết quả chạy bản `develop`

Lệnh gọi (bản develop nhận `question` qua query param):

```bash
curl -sS -i -X POST "http://localhost:8000/ask?question=Hello"
```

Output thực tế:

```http
HTTP/1.1 200 OK
date: Wed, 25 Nov 2026 08:15:38 GMT
server: uvicorn
content-length: 103
content-type: application/json

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 1.3: So sánh `develop` vs `production`

Test thực tế bản `production` (`PORT=8000 ENVIRONMENT=production AGENT_API_KEY=demo-local-key`):

```json
// GET /health
{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"production","timestamp":"2026-11-25T08:26:54.474644+00:00"}

// POST /ask (JSON body {"question":"Hello"})
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}
```

| Feature | Develop (`develop/app.py`) | Production (`production/app.py` + `config.py`) | Tại sao quan trọng? |
| --- | --- | --- | --- |
| **Config & Secrets** | Hardcode `OPENAI_API_KEY`, `DATABASE_URL`, `MAX_TOKENS`, `DEBUG` trong code | Đọc toàn bộ từ env vars qua `Settings`, fail-fast nếu thiếu `AGENT_API_KEY` khi `ENVIRONMENT=production` | Không lộ secret lên git; cùng một artifact/image chạy được ở dev, staging, production chỉ bằng cách đổi env (12-Factor). |
| **Host / Port / Reload** | `host="localhost"`, `port=8000`, `reload=True` | `host="0.0.0.0"`, `port=$PORT`, `reload=settings.debug` | Container cần bind `0.0.0.0` để nhận traffic từ bên ngoài; cloud cấp port động qua `PORT`; tắt `reload` để ổn định và tiết kiệm CPU/RAM. |
| **Health / Readiness** | Không có `/health`, `/ready` | `GET /health` (uptime, version, env), `GET /ready` (trả `503` khi `is_ready=False`), `GET /metrics` | Cloud platform cần liveness để tự restart khi treo và load balancer cần readiness để chỉ route traffic khi app đã sẵn sàng. |
| **Logging** | `print()` plain text, còn in cả `OPENAI_API_KEY` | `logging` xuất JSON có `time`, `level`, `event`, không log secret | Log JSON dễ parse, lọc, tìm kiếm trên hệ thống tập trung (Loki, Datadog, ELK) và tránh rò rỉ dữ liệu nhạy cảm. |
| **Shutdown** | Tắt đột ngột | `lifespan` hạ `is_ready=False` rồi hoàn thành request đang chạy trước khi thoát | Tránh lỗi 502/cắt ngang response của user khi deploy bản mới hoặc scale down. |
| **CORS & Validation** | Không cấu hình CORS, nhận `question` qua query | `CORSMiddleware` giới hạn `allowed_origins`, nhận JSON body và trả `422` nếu thiếu `question` | Giảm bề mặt tấn công cross-origin và tránh để input rỗng đi vào LLM gây tốn chi phí hoặc lỗi bất ngờ. |

---

## Part 2: Docker Containerization

### Exercise 2.1: Trả lời 4 câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image là gì?** `python:3.11` (full Debian + Python + compiler/build tools, nặng ~1 GB+). Bản `production/Dockerfile` chuyển sang `python:3.11-slim` gọn hơn nhiều.
2. **Working directory là gì?** `/app` (`WORKDIR /app`) — thư mục làm việc mặc định cho mọi lệnh `COPY`, `RUN`, `CMD` phía sau trong container.
3. **Tại sao `COPY requirements.txt` phải đứng trước `COPY . .` (hoặc `COPY app.py`)?** Để tận dụng **Docker layer cache**. `requirements.txt` ít thay đổi hơn code; khi chỉ sửa code app, layer `COPY requirements.txt` và layer `RUN pip install` (chậm nhất) vẫn hit cache nên build lại chỉ mất vài giây thay vì cài lại toàn bộ dependencies.
4. **`CMD` vs `ENTRYPOINT` khác nhau thế nào?** `ENTRYPOINT` định nghĩa tiến trình chính cố định của container (khó bị ghi đè trừ khi truyền `--entrypoint`), còn `CMD` cung cấp lệnh hoặc tham số mặc định có thể bị ghi đè trực tiếp khi truyền đối số sau tên image ở `docker run <image> <args>`. Kết hợp cả hai: `ENTRYPOINT` là binary chính, `CMD` là flags mặc định.

### Exercise 2.2: Build và chạy container `my-agent:develop`

Build từ gốc repo (`docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .`), chạy container map cổng `8000:8000` và gọi thử:

```bash
curl -sS -X POST "http://localhost:8000/ask?question=What+is+Docker%3F"
```

Output thực tế:

```json
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
```

### Exercise 2.3: Multi-stage build & so sánh kích thước image

- **Stage 1 (`builder`):** dùng `python:3.11-slim`, cài công cụ build (`gcc`, `libpq-dev`) và `pip install --no-cache-dir --user -r requirements.txt` vào `/root/.local`.
- **Stage 2 (`runtime`):** bắt đầu từ `python:3.11-slim` sạch, tạo non-root user `appuser`, chỉ `COPY --from=builder /root/.local` + `main.py` + `utils/mock_llm.py`, thiết lập `HEALTHCHECK` và chạy `uvicorn`.
- **Tại sao image nhỏ hơn?** Loại bỏ toàn bộ OS tooling thừa của `python:3.11` full image cùng `gcc`, header files (`libpq-dev`), apt cache ở stage builder; runtime chỉ giữ Python runtime và thư viện đã cài, đồng thời giảm diện tích tấn công bảo mật.

Số đo thực tế bằng `docker images my-agent` (Docker `29.1.3` trên WSL2 Ubuntu 24.04):

```text
REPOSITORY   TAG        SIZE
my-agent     advanced   240MB
my-agent     develop    1.67GB
```

- **Develop (`my-agent:develop`):** `1.67 GB` (`1,670 MB`; content size `426.3 MB`)
- **Production (`my-agent:advanced`):** `240 MB` (content size `57.4 MB`)
- **Chênh lệch:** giảm **`85.6%`** dung lượng image (`(1670 - 240) / 1670 = 85.6%`).

### Exercise 2.4: Docker Compose stack (`02-docker/production/`)

**Sơ đồ kiến trúc (mạng nội bộ `internal`):**

```text
                   ┌─────────────────────────────┐
  Client ─────────▶│   Nginx (:80 / :443)        │  (reverse proxy, rate limit 10r/s, security headers)
                   └──────────────┬──────────────┘
                                  │ proxy_pass http://agent:8000
                                  ▼
                   ┌─────────────────────────────┐
                   │   Agent (:8000, internal)   │  (FastAPI + uvicorn --workers 2, non-root)
                   └──────┬───────────────┬──────┘
                          │               │
      redis://redis:6379  ▼               ▼  http://qdrant:6333
                 ┌─────────────┐   ┌──────────────┐
                 │ Redis :6379 │   │ Qdrant :6333 │
                 │ (redis_data)│   │(qdrant_data) │
                 └─────────────┘   └──────────────┘
```

Các service được start (output `docker compose -p k5-day14-docker ps`):

```text
NAME                       IMAGE                   COMMAND                  SERVICE   STATUS                            PORTS
k5-day14-docker-agent-1    k5-day14-docker-agent   "uvicorn main:app --…"   agent     Up 2 seconds (health: starting)   8000/tcp
k5-day14-docker-nginx-1    nginx:alpine            "/docker-entrypoint.…"   nginx     Up 1 second                       0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
k5-day14-docker-qdrant-1   qdrant/qdrant:v1.9.0    "./entrypoint.sh"        qdrant    Up 12 seconds (healthy)           6333-6334/tcp
k5-day14-docker-redis-1    redis:7-alpine          "docker-entrypoint.s…"   redis     Up 12 seconds (healthy)           6379/tcp
```

Giao tiếp: các container cùng nằm trong bridge network `internal` nên gọi nhau bằng tên service DNS (`agent:8000`, `redis:6379`, `qdrant:6333`). Chỉ `nginx` publish cổng `80`/`443` ra host; `agent`, `redis`, `qdrant` đều cô lập nội bộ. `agent` dùng `depends_on` với `condition: service_healthy` chờ `redis` và `qdrant` healthy trước khi khởi động.

Kết quả test qua Nginx:

```json
// GET http://localhost/health
{"status":"ok","uptime_seconds":0.4,"version":"2.0.0","timestamp":"2026-11-25T08:33:12.339502"}

// POST http://localhost/ask  {"question":"Explain microservices"}
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway Deployment (Agent Part 6 — `06-lab-complete/`)

- **Platform:** Railway (`railway.toml` với `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`) + Railway Redis service nối qua biến `REDIS_URL`.
- **Public URL:** `PENDING_RAILWAY_URL` *(cập nhật ngay sau khi deploy xong ở CP6)*
- **Screenshots:** [dashboard.png](screenshots/dashboard.png) · [running.png](screenshots/running.png) · [test.png](screenshots/test.png)
- **Chi tiết lệnh test & env vars:** xem [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.2: So sánh `railway.toml` vs `render.yaml`

| Tiêu chí | `railway.toml` (Railway) | `render.yaml` (Render Blueprint) | Vì sao khác nhau? |
| --- | --- | --- | --- |
| **Định dạng & phạm vi** | TOML, cấu hình **1 service** (`[build]`, `[deploy]`) | YAML, **Infrastructure as Code đa service** (`services:` có thể khai báo cả `type: web` và `type: redis` cùng lúc) | Railway quản lý topology project (gắn thêm Redis, link biến) qua Dashboard/CLI; Render dùng Blueprint định nghĩa trọn stack trong 1 file để sync từ Git. |
| **Build & Runtime** | `builder = "DOCKERFILE"` hoặc `"NIXPACKS"` + `startCommand` | `runtime: docker` hoặc `runtime: python` kèm `buildCommand` và `startCommand` | Railway hỗ trợ auto-detect Nixpacks hoặc Dockerfile tối giản; Render khai báo rõ runtime, `region`, `plan` (`free`/`starter`...). |
| **Biến môi trường & Secret** | Không khai báo env/secret trong `railway.toml` — đặt trên Railway Dashboard hoặc `railway variables set` | Khai báo trực tiếp `envVars:` trong file; secret dùng `sync: false` (nhập tay trên dashboard) hoặc `generateValue: true` (Render tự sinh) | Cả hai đều tránh hardcode secret trong git, nhưng Render tách schema biến môi trường vào IaC còn Railway giữ cấu hình runtime tách khỏi file config. |
| **Health check & Restart** | `healthcheckPath = "/health"`, `healthcheckTimeout = 30`, `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | `healthCheckPath: /health`, `autoDeploy: true` (không có tham số retry policy chi tiết như Railway) | Railway cho kiểm soát trực tiếp chính sách restart container; Render tối ưu cho deploy tự động theo branch GitHub và chọn `plan`/`region`. |

### Exercise 3.3: GCP Cloud Run CI/CD (`cloudbuild.yaml` & `service.yaml`)

Pipeline trong `03-cloud-deployment/production-cloud-run/cloudbuild.yaml` chạy tuần tự 4 bước khi có commit mới:
1. **`test`:** dùng image `python:3.11-slim`, cài `requirements.txt` + `pytest` và chạy toàn bộ unit test (`pytest tests/ -v`) — fail là dừng pipeline ngay.
2. **`build`:** build Docker image gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, tận dụng `--cache-from` từ image `latest` để tăng tốc.
3. **`push`:** đẩy cả hai tag image lên Google Container Registry (`gcr.io`).
4. **`deploy`:** gọi `gcloud run deploy` triển khai bản `$COMMIT_SHA` lên vùng `asia-southeast1` (`min-instances=1` tránh cold start, `max-instances=10`, `512Mi` RAM / `1` vCPU, timeout `60s`, nạp `OPENAI_API_KEY` từ Secret Manager). Song song, `service.yaml` mô tả cấu hình Knative Service khai báo (IaC) với `containerConcurrency: 80`, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready` và tham chiếu secret qua `secretKeyRef`.

---

## Part 4: API Security

### Exercise 4.1: API Key Authentication (`04-api-gateway/develop/`)

- **Kiểm tra ở đâu:** dependency `verify_api_key` đọc header `X-API-Key` bằng `APIKeyHeader(name="X-API-Key", auto_error=False)` và gắn vào `POST /ask` qua `Depends(verify_api_key)`.
- **Khi thiếu hoặc sai key:** thiếu header → HTTP `401 Unauthorized`; sai key → HTTP `403 Forbidden` (ở Part 6 chuẩn hóa trả `401` cho cả thiếu và sai key + dùng `hmac.compare_digest` tránh timing attack).
- **Cách rotate key an toàn:** lưu key trong env/Secret Manager; trong giai đoạn chuyển đổi chấp nhận tạm thời cả `AGENT_API_KEY_CURRENT` và `AGENT_API_KEY_NEXT` (overlap window), cập nhật dần các client sang key mới, xác nhận không còn traffic dùng key cũ rồi thu hồi key cũ.

Output `curl -i` thực tế (`AGENT_API_KEY=secret-key-123`):

```http
# Không có key -> 401
HTTP/1.1 401 Unauthorized
date: Wed, 25 Nov 2026 08:33:14 GMT
server: uvicorn
content-length: 67
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

# Có X-API-Key: secret-key-123 -> 200
HTTP/1.1 200 OK
date: Wed, 25 Nov 2026 08:33:14 GMT
server: uvicorn
content-length: 122
content-type: application/json

{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT Authentication (`04-api-gateway/production/`)

- **Flow JWT trong `auth.py`:** client gửi username/password (`student`/`demo123`) tới `POST /auth/token` → server xác thực và ký JWT bằng `HS256` chứa claims `sub`, `role`, `iat`, `exp` (hết hạn sau 60 phút) → client gửi `Authorization: Bearer <token>` ở `POST /ask` → `verify_token` kiểm tra chữ ký và hạn dùng stateless (không cần query DB mỗi request).

Output thực tế (đã che token):

```json
// POST /auth/token (student / demo123)
{"access_token": "<REDACTED_JWT>", "token_type": "bearer", "expires_in_minutes": 60, "hint": "Include in header: Authorization: Bearer <REDACTED_JWT>"}
```

```http
# Không có token -> 401
HTTP/1.1 401 Unauthorized
date: Wed, 25 Nov 2026 08:33:15 GMT
server: uvicorn
www-authenticate: Bearer
content-length: 76
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

# Có Authorization: Bearer <REDACTED_JWT> -> 200
HTTP/1.1 200 OK
date: Wed, 25 Nov 2026 08:33:15 GMT
server: uvicorn
content-length: 192
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}
```

### Exercise 4.3: Rate Limiting (`04-api-gateway/production/rate_limiter.py`)

- **Thuật toán:** **Sliding Window Log / Counter** dùng `collections.deque` lưu timestamp các request của từng user trong cửa sổ `window_seconds = 60` (xóa các timestamp `< now - 60`, kiểm tra `len(window) >= max_requests`).
- **Giới hạn:** `student` (`role="user"`) tối đa **10 req/phút** (`rate_limiter_user`); `teacher` (`role="admin"`) tối đa **100 req/phút** (`rate_limiter_admin`).
- **Bypass / nâng hạn mức cho admin:** trong `app.py` (dòng 141) phân nhánh theo claim `role` lấy từ JWT: `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user` (hoặc skip check hoàn toàn nếu `role == "admin"`).

Output vòng lặp `curl -i` thực tế với token `student` (req #1 đã gọi ở 4.2, gửi tiếp #2..#11):

```text
req #2: HTTP/1.1 200 OK
req #3: HTTP/1.1 200 OK
req #4: HTTP/1.1 200 OK
req #5: HTTP/1.1 200 OK
req #6: HTTP/1.1 200 OK
req #7: HTTP/1.1 200 OK
req #8: HTTP/1.1 200 OK
req #9: HTTP/1.1 200 OK
req #10: HTTP/1.1 200 OK
req #11: HTTP/1.1 429 Too Many Requests
```

Full response `429` ở request thứ 11:

```http
HTTP/1.1 429 Too Many Requests
date: Wed, 25 Nov 2026 08:33:17 GMT
server: uvicorn
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795595657
retry-after: 59
content-length: 98
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

### Exercise 4.4: Cost Guard — Cách tiếp cận và vì sao cần cả Rate Limit lẫn Cost Guard

- **Tại sao cần cả hai:** Rate limit bảo vệ **tốc độ** (chống spam/DDoS, giới hạn số request/phút), còn Cost guard bảo vệ **tiền** (một request prompt dài hoặc gọi model đắt vẫn lọt qua rate limit nhưng đốt ngân sách LLM nhanh chóng).
- **Cách triển khai trong production (`06-lab-complete/app/cost_guard.py`):**
  1. **Trước khi gọi LLM (`check_budget()`):** đọc tổng chi tiêu từ Redis theo key theo ngày (`cost:daily:YYYY-MM-DD`) và theo tháng (`cost:monthly:YYYY-MM`). Nếu đạt/vượt `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` → chặn ngay với HTTP **`402 Payment Required`**, không gọi LLM.
  2. **Sau khi gọi LLM (`record_cost(input_tokens, output_tokens)`):** tính chi phí `cost = (in/1000)*0.00015 + (out/1000)*0.0006`, cộng nguyên tử vào Redis qua pipeline `INCRBYFLOAT` + `EXPIRE` (48 giờ cho key ngày, 35 ngày cho key tháng) để tự reset chu kỳ mới và giữ state đồng nhất giữa nhiều worker/instance.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health Check (`/health`) vs Readiness (`/ready`)

- **`GET /health` (Liveness probe):** kiểm tra process còn sống không. Nếu fail hoặc timeout → platform (Docker/K8s/Railway) sẽ **restart container**. Phải luôn trả `200` khi process chạy bình thường, kể cả khi dependency ngoài (như Redis) tạm đứt, tránh vòng lặp restart vô ích (crash loop).
- **`GET /ready` (Readiness probe):** kiểm tra instance có đủ điều kiện nhận traffic không (đã load xong, không đang shutdown, kết nối được Redis/DB). Nếu trả `503` → load balancer **ngừng route request tới instance đó nhưng không restart**.

Output thực tế trên `05-scaling-reliability/develop/app.py`:

```json
// GET /health
{"status":"ok","uptime_seconds":0.6,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:33:19.126588+00:00","checks":{"memory":{"status":"ok","used_percent":8.4}}}

// GET /ready
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful Shutdown (`SIGTERM`)

Khi orchestrator gửi `SIGTERM`: app hạ readiness (`_is_ready = False`) để load balancer dừng gửi request mới, uvicorn ngừng nhận kết nối mới nhưng giữ các request in-flight chạy đến khi trả `200 OK`, chạy khối shutdown của `lifespan` (đóng connection Redis), rồi thoát êm với exit code `0`. Lưu ý: nếu tự đăng ký `signal.signal(signal.SIGTERM, ...)` thì phải gọi lại handler cũ của uvicorn (hoặc để uvicorn tự handle qua `lifespan`), nếu không process sẽ treo và bị `SIGKILL` (exit 137).

Output test thực tế (gửi `POST /ask?question=Long+task` song song với `kill -TERM`):

```text
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."} [HTTP 200]
INFO:     Shutting down
INFO:     127.0.0.1:50044 - "POST /ask?question=Long+task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 08:33:22,272 INFO 🔄 Graceful shutdown initiated...
2026-11-25 08:33:22,272 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [4062]
```

### Exercise 5.3: Stateless Design

Lưu state trong RAM (`conversation_history = {}`, `rate_limiter` in-memory) sẽ hỏng ngay khi chạy nhiều worker hoặc nhiều container sau load balancer: request 1 rơi vào instance A (lưu vào RAM của A), request 2 bị Nginx chuyển sang instance B (không thấy lịch sử / reset bộ đếm rate limit), và restart container là mất sạch state. Chuyển toàn bộ session, rate limit (`ZSET`) và cost counter (`INCRBYFLOAT`) sang **Redis** giúp mọi instance đều giống hệt nhau (stateless), scale ngang tự do và không mất state khi restart.

### Exercise 5.4 & 5.5: Load Balancing (`--scale agent=3`) & Test Stateless

Chạy stack trong `05-scaling-reliability/production/` với `docker compose -p k5-day14-scaling up -d --build --scale agent=3`:

```text
NAME                       IMAGE                    SERVICE   STATUS                    PORTS
k5-day14-scaling-agent-1   k5-day14-scaling-agent   agent     Up 11 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   k5-day14-scaling-agent   agent     Up 11 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   k5-day14-scaling-agent   agent     Up 11 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   nginx:alpine             nginx     Up 10 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   redis:7-alpine           redis     Up 22 seconds (healthy)   6379/tcp
```

Output thực tế chạy `python test_stateless.py`:

```text
============================================================
Stateless Scaling Demo
============================================================

Session ID: f5769adb-3470-4e2e-b1af-8d7db3b5a126

Request 1: [instance-4f015b]
  Q: What is Docker?
  A: Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!...

Request 2: [instance-bff4ca]
  Q: Why do we need containers?
  A: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận....

Request 3: [instance-4f015b]
  Q: What is Kubernetes?
  A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

Request 4: [instance-c9a7e5]
  Q: How does load balancing work?
  A: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận....

Request 5: [instance-bff4ca]
  Q: What is Redis used for?
  A: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé....

------------------------------------------------------------
Total requests: 5
Instances used: {'instance-bff4ca', 'instance-c9a7e5', 'instance-4f015b'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  [user]: Why do we need containers?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...
  [user]: What is Kubernetes?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...
  [user]: How does load balancing work?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...
  [user]: What is Redis used for?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...

✅ Session history preserved across all instances via Redis!
```

**Giải thích kết quả:** 5 request trong cùng một `session_id` được Nginx phân phối đều sang cả 3 container khác nhau (`instance-4f015b`, `instance-bff4ca`, `instance-c9a7e5`), nhưng `GET /chat/{session_id}/history` vẫn trả về đủ 10 message (5 cặp user/assistant) vì toàn bộ lịch sử hội thoại được lưu tập trung trong Redis thay vì bộ nhớ riêng của từng container.

---

## Part 6: Final Project — Output `check_production_ready.py`

Chạy đầy đủ trong `06-lab-complete/` trên WSL2 Ubuntu 24.04 với Docker `29.1.3`:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 41/41 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số chi tiết đo được từ script chấm:
- Docker image multi-stage (`python:3.11-slim`, user non-root `appuser`): **56 MB** (< 500 MB).
- Rate limiting (`RATE_LIMIT_PER_MINUTE=5`, 2 uvicorn workers): trả `429` sau đúng 5 request thành công, restart agent vẫn trả `429` (state lưu trong Redis qua Lua sliding window).
- Cost guard: trả `402` khi budget `0.000001 USD`.
- Structured logging: `17` dòng log JSON hợp lệ.
- Graceful shutdown: nhận `SIGTERM` khi đang có request in-flight vẫn trả `200`, thoát `exit code 0`; `docker stop` kết thúc trong **`1.0s`** với `exit code 0`.

---

## AI Log

1. **Setup Docker Engine trong WSL2 và sửa lỗi TLS pull image qua Cloudflare WARP:**
   - *Hỏi:* Cách cài Docker Engine trên WSL2 Ubuntu 24.04 và nguyên nhân `docker pull` gặp `net/http: TLS handshake timeout` trên `auth.docker.io` trong khi `curl` gọi được bình thường.
   - *Nhận:* Hướng dẫn cài `docker.io` + `docker-compose-v2`, đồng thời chẩn đoán xung đột MTU 1280 của Cloudflare WARP với TLS certificate chưa nén của Go `crypto/tls`, đề xuất đặt `ip link set dev eth0 mtu 1200` + `"mtu": 1200` trong `/etc/docker/daemon.json`.
   - *Tự kiểm tra:* Cấu hình systemd service `wsl-mtu.service`, restart Docker, pull thành công `nginx:alpine`, `python:3.11`, `qdrant/qdrant:v1.9.0`, `redis:7-alpine`.
2. **Hoàn thiện các TODO của Part 6 (`06-lab-complete/`):**
   - *Hỏi:* Gợi ý cấu trúc sliding window atomic trên Redis (Lua script) cho nhiều uvicorn workers, cost guard theo ngày/tháng, `JsonFormatter` cho logging, Dockerfile multi-stage với `uvicorn` làm PID 1, và `railway.toml`.
   - *Nhận:* Các module `app/config.py`, `app/redis_client.py`, `app/auth.py`, `app/rate_limiter.py`, `app/cost_guard.py`, `app/main.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `railway.toml`.
   - *Tự sửa & tự kiểm tra:* Đọc lại từng tiêu chí trong `06-lab-complete/README.md`, chạy `python check_production_ready.py --static-only` (26/26) rồi chạy bản đầy đủ trong WSL2 với Docker cho đến khi đạt `PASS 12/12` (41/41 checks).
3. **Part 1–5 và Part 7 (soát cấu trúc báo cáo & sửa `KNOWLEDGE_BASE`):**
   - *Hỏi:* Gợi ý cấu trúc bảng so sánh `railway.toml` vs `render.yaml`, phân tích `cloudbuild.yaml`/`service.yaml`, bổ sung metric `agent_question_length_chars` và workflow `.github/workflows/eval.yml`.
   - *Nhận:* Khung trình bày câu trả lời và đoạn code mẫu Histogram / GitHub Actions.
   - *Tự kiểm tra:* Tự chạy toàn bộ lệnh thực nghiệm trên máy (Part 1, 2, 4, 5, 7), đối chiếu từng con số (MB image, HTTP status/headers, RPS, p50/p95/p99, accuracy, CSV Locust, trace Jaeger, alert Prometheus) từ file output thật, không ước lượng hay bịa số liệu.
