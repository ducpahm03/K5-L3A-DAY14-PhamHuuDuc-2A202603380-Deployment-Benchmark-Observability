# Deployment Information

## Public URL
https://ai-agent-production-4db2828e.up.railway.app

## Platform
Railway (`day14-ai-agent` — service `ai-agent` built từ `06-lab-complete/Dockerfile` theo `railway.toml` + service `Redis` nội bộ nối qua `REDIS_URL`).

## Test Commands

Đặt biến môi trường trước khi chạy (không hardcode key vào lệnh):

```bash
export BASE="https://ai-agent-production-4db2828e.up.railway.app"
export AGENT_API_KEY="<YOUR_AGENT_API_KEY>"
```

### 1. Bộ 4 lệnh tự kiểm chuẩn CP6 (chỉ in HTTP status code, không lộ key)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
# Expected & Observed: 200

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
# Expected & Observed: 200

curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'
# Expected & Observed: 401

curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"question":"Hello"}'
# Expected & Observed: 200
```

### 2. Health & Readiness Check (xem JSON body)

```bash
curl -sS "$BASE/health"
# Observed: {"status":"ok","environment":"production"}

curl -sS "$BASE/ready"
# Observed: {"status":"ready","redis":"connected"}
```

### 3. API Test (with authentication & validation)

```bash
curl -sS -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
# Observed (200): {"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","user_id":"test","cost_usd":1.05e-05}

curl -sS -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"invalid": "data"}'
# Observed (422): {"error":"validation_error","detail":[{"field":"body.question","msg":"Field required"}]}
```

## Environment Variables Set (trên Railway Dashboard / CLI, không commit vào Git)

- `PORT` *(do Railway tự inject)*
- `ENVIRONMENT=production`
- `LOG_LEVEL=INFO`
- `REDIS_URL=${{Redis.REDIS_URL}}` *(tham chiếu trực tiếp tới service Redis trong cùng project Railway)*
- `AGENT_API_KEY` *(secret cấu hình trực tiếp trên Railway, không ghi trong code)*
- `RATE_LIMIT_PER_MINUTE=10`
- `DAILY_BUDGET_USD=1.0`
- `MONTHLY_BUDGET_USD=10.0`

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
