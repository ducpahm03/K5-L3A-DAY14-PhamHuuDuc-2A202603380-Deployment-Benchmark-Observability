# Benchmark & Observability Report — Day 14

> Mọi con số dưới đây được đo trực tiếp từ lệnh chạy thật và khớp với các file trong `reports/`, `benchmark-results/` và `screenshots/`.

**Họ tên:** Pham Huu Duc (MSSV: 2A202603380)  
**Ngày chạy:** 2026-11-25  
**Môi trường:** Windows 11 + WSL2 Ubuntu 24.04.5 LTS · Python 3.12.3 · Docker Engine 29.1.3 / Docker Compose v2.40.3

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
| --- | --- | --- |
| p95 latency `/ask` | `< 500 ms` | Đảm bảo 95% người dùng nhận phản hồi mượt (< 0.5s), trong khi baseline mock LLM v2 bình thường ~180–220ms; ngưỡng 500ms đủ chừa biên cho jitter nhưng bắt được khi tail latency tràn sang p95 hoặc server bão hòa. |
| Error rate `/ask` | `< 2.0 %` | Cho phép error budget 2% (lớn hơn tỷ lệ lỗi nền ~1% của upstream mock LLM để không báo động giả, nhưng bắt kịp sự cố provider 503 tăng cao). |
| Accuracy trên golden dataset | `≥ 85 %` (mục tiêu sau sửa KB: `100 %`, 0 regression) | Ngưỡng chặn merge tối thiểu 85% trong CI quality gate (`.github/workflows/eval.yml`) giúp ngăn bản prompt/retrieval kém lọt ra production. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, 300 requests/mức)

Nguồn dữ liệu: `reports/load_v2_c1.json`, `reports/load_v2_c10.json`, `reports/load_v2_c50.json`, `reports/load_v2_c100.json` (SLO: `p95 ≤ 500ms`, `error rate ≤ 2.0%`).

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 4.68 | 192.2 | 218.7 | 1681.1 | 0.33% (1/300) | PASS |
| 10 | 44.40 | 187.8 | 216.8 | 1665.2 | 0.33% (1/300) | PASS |
| 50 | 104.78 | 196.2 | 241.6 | 1695.3 | 1.00% (3/300) | PASS |
| 100 | 56.82 | 375.4 | 3147.8 | 3522.8 | 1.00% (3/300) | FAIL (`p95 > 500ms`) |

**Nhận xét:** RPS **không** tăng tuyến tính theo concurrency. Từ `c=1 → 10`, RPS tăng gần tuyến tính (`4.68 → 44.40 req/s`, ~9.5×) vì tác vụ chủ yếu là chờ I/O async (`asyncio.sleep`) và event loop vẫn còn dư tài nguyên. Lên `c=50`, RPS chỉ còn tăng ~2.4× (`104.78 req/s`) trong khi p95 bắt đầu nhích lên (`241.6 ms`). Tới `c=100`, hệ thống 1 process uvicorn chạm điểm bão hòa (xếp hàng connection/event loop), khiến throughput **tụt ngược xuống `56.82 req/s`** còn `p50` gần gấp đôi (`375.4 ms`) và `p95` vọt lên **`3147.8 ms`** (vi phạm SLO).

### 2.2 Tail latency (`--requests 300 --concurrency 20`, agent v2)

Nguồn dữ liệu: `reports/load_v2_slow_0.json`, `reports/load_v2_slow_0.03.json`, `reports/load_v2_slow_0.1.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) | RPS | SLO |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 186.7 | 216.1 | 222.1 | 104.83 | PASS |
| 0.03 (mặc định) | 220.1 | 220.4 | 1684.4 | 75.22 | PASS |
| 0.1 | 320.4 | 1693.4 | 1715.6 | 50.08 | FAIL (`p95 > 500ms`) |

**Nhận xét:** **p99** và **p95** là hai chỉ số nhạy nhất với request chậm:
- Khi tăng `MOCK_SLOW_RATE` từ `0 → 0.03` (chỉ 3% request chậm), `avg` chỉ tăng nhẹ `+33.4 ms` (`186.7 → 220.1 ms`), trông hoàn toàn ổn, nhưng **`p99` nhảy vọt `+1462.3 ms` (`222.1 → 1684.4 ms`)**.
- Khi tăng lên `0.1` (10% request chậm > ngưỡng 5%), `avg` mới là `320.4 ms` (vẫn dưới 500ms nếu dùng average làm SLO!), trong khi **`p95` đã bùng lên `1693.4 ms`** — tức cứ 20 người dùng có ít nhất 1 người phải đợi gần 1.7 giây.
- Do đó SLO **phải đặt theo percentile (p95/p99)**, không đặt theo average vì trung bình bị pha loãng bởi đa số request nhanh và che giấu nhóm người dùng gặp trải nghiệm tệ nhất (tail latency).

### 2.3 Ghi chú Exercise 7.1 (`/metrics` & cardinality)

- **Ví dụ metric đo được từ `GET /metrics`:**
  - *Counter:* `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total`, `llm_cost_usd_total` (chỉ tăng).
  - *Gauge:* `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0` (lên xuống theo trạng thái hiện tại).
  - *Histogram:* `http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"}`, `agent_question_length_chars_bucket{le="50.0"} 1.0` (chia bucket để tính quantile).
- **Vì sao dùng `route.path` thay vì `request.url.path`:** Nếu dùng URL thật làm label `route`, khi bot quét hàng chục nghìn đường dẫn lạ (`/wp-admin`, `/.env`, `/a1b2c3`...), mỗi URL sinh ra một time series mới → **bùng nổ cardinality**, làm Prometheus tốn RAM/OOM và query chậm đi nghiêm trọng.
- **Metric đã bổ sung:** Histogram `agent_question_length_chars` (`buckets=(20, 50, 100, 200, 500, 1000, 2000)`) trong `06-lab-complete/benchmark-observability/develop/app.py` (dùng Histogram thay vì label độ dài để tránh cardinality cao).

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Nguồn dữ liệu: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json` (23 câu hỏi trên `eval/golden_dataset.jsonl`).

| Chỉ số | Agent v1 (`eval_v1.json`) | Agent v2 (`eval_v2.json`) | Agent v2 sau khi sửa KB (`eval_v2_fixed.json`) |
| --- | ---: | ---: | ---: |
| Accuracy | 4.35% (1/23) | 91.30% (21/23) | **100.00% (23/23)** |
| Avg tokens / câu | 57.7 | 108.8 | 113.7 |
| Latency p50 / p95 (ms) | 160.1 / 189.2 | 178.4 / 209.1 | 192.2 / 217.8 |
| Tổng cost (USD) | `$0.000412` | `$0.000758` | `$0.000790` |
| Cost / câu đúng (USD) | `$0.00041175` | `$0.00003607` | **`$0.00003436`** |
| Regressions | — | 0 (so với v1, +20 câu đúng) | **0** (so với v2, +2 câu đúng) |
| Quality gate | **FAIL** | **PASS** (≥ 80%) | **PASS** (≥ 100%, 0 regression) |

**Những câu v2 trả lời sai ban đầu và cách sửa (trong `utils/mock_llm.py` — `KNOWLEDGE_BASE`):**

| ID | Keyword bị thiếu | Đã sửa gì |
| --- | --- | --- |
| `security-02` | `token`, `hết hạn`, `chữ ký` | Thêm entry `(["jwt"], ...)` vào `KNOWLEDGE_BASE` (cả `develop/` và `production/`) giải thích sự khác nhau giữa API key cố định và JWT là token có chữ ký số, chứa thông tin user và thời điểm hết hạn. |
| `scaling-03` | `cpu`, `memory`, `custom metrics` | Thêm entry `(["hpa", "kubernetes", "autoscal"], ...)` vào `KNOWLEDGE_BASE` giải thích Kubernetes HPA scale số pod dựa trên CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi). |

**Nhận xét trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- Agent v2 thêm bước retrieval (RAG) nên **tốn gần gấp đôi token/câu** (`57.7 → 108.8` và `113.7` sau sửa KB), **tổng cost cho 23 câu tăng ~1.9×** (`$0.000412 → $0.000790`), và **latency p50 tăng thêm ~20–30 ms** (bước tìm tài liệu ~20ms + prompt dài hơn).
- Tuy nhiên, độ chính xác nhảy từ **4.35% → 100.0%**, khiến chỉ số **`cost / câu đúng` giảm ~12 lần** (`$0.00041175 → $0.00003436`). Đánh đổi +20ms và +1.9× tổng chi phí để đổi lấy câu trả lời đúng có giá trị gấp 12 lần trên mỗi đồng chi ra.

**Quality gate trong CI (Exercise 7.3):**
- Kiểm chứng thực tế: chạy `python eval/run_eval.py --url http://127.0.0.1:8001 --baseline reports/eval_v2.json` (chấm v1 so với baseline v2) báo **20 regressions**, `Quality gate: ❌ FAIL` và trả **`exit code = 1`**.
- Đã cấu hình GitHub Actions workflow tại [`.github/workflows/eval.yml`](.github/workflows/eval.yml) chạy tự động khi mở `pull_request`: khởi động agent v2 (`MOCK_ERROR_RATE=0`) và gọi `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85`. Khi accuracy dưới 85% hoặc phát hiện regression so với baseline, `run_eval.py` thoát với `exit code 1` → job CI fail → GitHub chặn merge PR lên `main`.

---

## 4. Observability stack (production)

### 4.1 Screenshot & quan sát Exercise 7.4

- [x] Dashboard Grafana khi đang có tải (4 Golden Signals, Latency & Traffic, LLM, Instances): [grafana-dashboard.png](screenshots/grafana-dashboard.png)
- [x] Một trace chậm trong Jaeger (`chat mock-llm-small` chiếm 98.7%): [jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception` + stacktrace `LLMError`): [jaeger-error-trace.png](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với cả 4/4 alert đang **firing**: [prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png)

**Trả lời các câu hỏi Exercise 7.4 (số đo thật từ Prometheus & Grafana lúc chạy 30 users / 2 instance):**
1. **p95 và avg:** `p95 = 293.1 ms` (trên tile Grafana lúc chụp: `291.60 ms`), `avg = 231.7 ms`, trong khi đường `p99` trên biểu đồ vọt lên `~1.8s`. Khoảng cách lớn giữa `avg`/`p50` và `p99` phản ánh rõ tail latency (~3% request chậm +1.5s) mà giá trị trung bình không thể hiện hết.
2. **Requests/s theo instance:** `172.18.0.2:8000 = 3.69 req/s` và `172.18.0.3:8000 = 3.70 req/s` — Nginx round-robin chia tải gần như tuyệt đối đều 50/50 giữa 2 instance.
3. **Vì sao `input tokens/s` (`2300.1 tok/s`) lớn hơn `output tokens/s` (`1202.9 tok/s`, gấp ~1.91×):** Mỗi request ở agent v2 gửi kèm `SYSTEM_PROMPT` + tài liệu context lấy từ bước RAG retrieval + câu hỏi vào input, còn output chỉ là đoạn trả lời ngắn gọn.
4. **`curl -i http://localhost:8080/metrics` trả về gì và vì sao:** Trả về **`HTTP/1.1 404 Not Found`** từ Nginx (`location = /metrics { return 404; }` trong `nginx.conf`). Endpoint `/metrics` chỉ để Prometheus scrape nội bộ trong Docker network `obs_net`, phải chặn từ bên ngoài vì metrics làm lộ cấu trúc route, lưu lượng, tỷ lệ lỗi và chi phí hệ thống.

### 4.2 Thí nghiệm "phá hệ thống" (Chaos & Alerts)

Unit test alert rules (`docker run --rm -v "$PWD/prometheus:/p" --entrypoint promtool prom/prometheus:v3.16.0 test rules /p/alert_rules_test.yml`): **`SUCCESS`**.

Thí nghiệm A, B, C, D chạy với Locust 40 users trên stack 3 instance (`-p k5-day14-observability`), đo thời gian chính xác từ lúc áp dụng lỗi (`T0`) qua API `/api/v1/alerts` của Prometheus; thí nghiệm E chạy riêng sau khi khôi phục stack.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
| --- | --- | --- | --- | --- |
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (`for: 1m`) | **`83s`** (`pending` ở `+27s`) | Error rate (Golden Signals đỏ > 5%), Requests/s theo status code (`503`), LLM calls (`error`) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (`for: 2m`) | **`134s`** (`pending` ở `+17s`) | Latency p95 (Golden Signals đỏ > 1s), Latency `/ask` p50/p95/p99, LLM latency p95 |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (`for: 2m`) | **`139s`** (`pending` ở `+22s`) | Cost burn rate (USD/giờ vượt ngưỡng `$2/giờ`), LLM cost 1h, LLM latency p95 |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (`for: 30s`) | **`41s`** sau khi pause (`+43s` từ T0) | Instances up giảm 1, Requests/s theo instance mất lưu lượng từ instance bị pause |
| E | `AGENT_VERSION=v1` | **Không có alert nào firing** | — (0 alerts) | `Retrieval hit ratio` mất hoàn toàn (`rate(agent_retrieval_total[1m]) = 0`), `Tokens/s` giảm, bảng `agent_info` đổi sang `version="v1"` |

**Phân tích thí nghiệm D (`docker pause` & lỗi 504 ở Nginx):**
- Dùng `docker pause` thay vì `docker stop` vì container bị pause vẫn tồn tại trong Docker DNS nhưng treo không phản hồi scrape → Prometheus ghi nhận `up{job="agent"} == 0` và bắn `AgentInstanceDown` (còn `docker stop` xóa IP khỏi DNS nên target biến mất chứ không báo down).
- Lỗi `504 Gateway Timeout` xảy ra tại tầng Nginx (khi chờ vượt `proxy_read_timeout 10s`), request không bao giờ đi tới bước ghi nhận response của agent → **panel Error rate của agent không thấy lỗi 504 này**. Do đó production bắt buộc phải giám sát thêm metrics của Load Balancer/Nginx hoặc dùng synthetic probe từ bên ngoài.

**Thí nghiệm E không làm alert nào firing — cách phát hiện loại sự cố này trong production:**
- Alert hạ tầng (error rate, p95, uptime, cost) vẫn xanh 100% dù chất lượng trả lời rớt từ 100% xuống 4.35%.
- Để phát hiện trong production cần kết hợp:
  1. **CI Quality Gate trước deploy:** chạy `eval/run_eval.py` trên golden dataset chặn merge nếu accuracy tụt hoặc có regression.
  2. **Theo dõi chỉ số chất lượng đặc thù RAG/AI:** đặt alert cho `retrieval_hit_ratio` tụt đột ngột (vd `< 50%` trong 5 phút) và theo dõi `agent_info{version}` thay đổi bất thường.
  3. **Online evaluation trên traffic thật:** lấy mẫu (sampling) request production chấm tự động bằng LLM-as-judge, kết hợp thu thập tín hiệu người dùng (thumbs up/down, tỷ lệ hỏi lại/bỏ phiên).

### 4.3 Debug bằng trace (Exercise 7.5)

**Trace chậm nhất tìm được trong Jaeger (`screenshots/jaeger-slow-trace.png`):**
- `trace_id`: `f6fa84fb0d7a8f9cb4af14b6e292b14b`
- Tổng thời gian: **`1720.4 ms`** (hiển thị `1.7s` trên Jaeger UI)
- Breakdown các span:
  - `POST /ask` (root): `1720.4 ms` (`100%`)
  - `agent.run`: `~1719 ms`
  - `agent.retrieve_context`: `20.7 ms` (`1.2%`)
  - **Span chiếm nhiều thời gian nhất:** **`chat mock-llm-small`** — **`1698.2 ms` (`98.7%`)** (`input_tokens=95`, `output_tokens=55`, `cost_usd=0.000047`). Kết luận: nút thắt nằm ở upstream LLM call chậm bất thường (tail latency), không phải bước retrieval hay code FastAPI.
- Dòng log JSON tương ứng (trích từ `docker compose -p k5-day14-observability logs agent` khớp đúng `trace_id`):

```json
{"ts": "2026-11-25T08:52:31.905094+00:00", "level": "INFO", "service": "observable-agent", "instance": "d67400c8dbbe", "event": "llm_call", "trace_id": "f6fa84fb0d7a8f9cb4af14b6e292b14b", "span_id": "f3f519ffb5db5d36", "request_id": "65447afeae7d", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 95, "output_tokens": 55, "cost_usd": 4.7e-05, "llm_ms": 1698.2}
{"ts": "2026-11-25T08:52:31.905534+00:00", "level": "INFO", "service": "observable-agent", "instance": "d67400c8dbbe", "event": "http_request", "trace_id": "f6fa84fb0d7a8f9cb4af14b6e292b14b", "span_id": "f3f519ffb5db5d36", "request_id": "65447afeae7d", "method": "POST", "route": "/ask", "status": 200, "ms": 1720.4}
```

**Trace lỗi đối chiếu thêm (`screenshots/jaeger-error-trace.png`):**
- `trace_id`: `8d5328aaeacf1e0c88ef78159187046a` (`POST /ask` → `503`, `109.4 ms`, span `chat mock-llm-small` ghi event `exception`: `utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`):

```json
{"ts": "2026-11-25T08:53:19.627517+00:00", "level": "INFO", "service": "observable-agent", "instance": "d67400c8dbbe", "event": "llm_error", "trace_id": "8d5328aaeacf1e0c88ef78159187046a", "span_id": "1c6ddd5f0234e48d", "request_id": "3c013c93dae6", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Nguồn dữ liệu: `benchmark-results/scale_1_stats.csv` và `benchmark-results/scale_3_stats.csv`.

**Theo endpoint `POST /ask`:**

| Số instance | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 7,170 | 60.02 | 190 | 230 | 1700 | 80 (1.12%) |
| 3 | 7,205 | 60.33 | 190 | 230 | 1700 | 77 (1.07%) |

**Tổng hợp (`Aggregated`, gồm `POST /ask` + `GET /health`):**

| Số instance | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 7,885 | 66.00 | 190 | 230 | 1700 | 80 (1.01%) |
| 3 | 7,923 | 66.34 | 190 | 230 | 1700 | 77 (0.97%) |

**Giải thích kết quả:** Scale từ 1 lên 3 instance **không cải thiện p95 (`230 ms → 230 ms`) hay p99 (`1700 ms → 1700 ms`)**, và RPS gần như giữ nguyên (`60.02 → 60.33 req/s` ở `/ask`, `66.00 → 66.34 req/s` tổng). Lý do:
1. **Nút thắt nằm ở upstream LLM latency (I/O-bound)** — mỗi request dành ~98% thời gian chờ phản hồi LLM (`~190 ms` nền, ~3% tail `+1500 ms`), không phải CPU của FastAPI. Thêm container không thể làm nhà cung cấp LLM trả lời nhanh hơn.
2. **Tải đầu vào của kịch bản 100 user có `wait_time(0.5, 2.0)` bị chặn trần ở ~66–68 RPS** (`100 users / (1.25s nghỉ + 0.23s xử lý)`). Một instance FastAPI async đủ sức xử lý mức I/O này mà chưa chạm ngưỡng bão hòa CPU/event loop (khác với `load_test.py --concurrency 100` không có thời gian nghỉ ở mục 2.1). Tuy vậy, chạy 3 instance vẫn mang lại lợi ích rõ ràng về **High Availability** (1 instance chết hoặc rolling deploy vẫn còn 2 instance phục vụ) và chịu tải đột biến tốt hơn khi traffic vượt ngưỡng CPU của 1 instance.

---

## 6. Kết luận

- **3 điều học được về benchmark & observability:**
  1. **Average che giấu tail latency:** chỉ cần 3–10% request chậm là p95/p99 tăng gấp 7–8 lần dù average vẫn trông bình thường — SLO latency bắt buộc đặt theo p95/p99.
  2. **Metric hạ tầng xanh không đồng nghĩa agent trả lời đúng:** đổi xuống agent v1 làm accuracy tụt từ 100% xuống 4.35% mà không một alert hạ tầng nào kêu; cần kết hợp CI eval quality gate + giám sát chất lượng (retrieval hit ratio, online eval) song song với 4 Golden Signals.
  3. **Liên kết 3 trụ cột Metrics → Traces → Logs giúp khoanh vùng nút thắt trong vài giây:** Grafana báo p95 tăng → Jaeger chỉ ra span `chat mock-llm-small` chiếm 98.7% thời gian → grep `trace_id` trong JSON logs cho đầy đủ ngữ cảnh request, token và lỗi.
- **Nếu đưa agent này lên production thật, cần thêm các metric / alert:**
  - **Nginx / Load Balancer metrics (`nginx_http_requests_total{status="504"}` và synthetic uptime probe bên ngoài):** để bắt các lỗi timeout/502/504 mà bản thân container agent không nhìn thấy.
  - **RAG & LLM Quality metrics:** alert khi `retrieval_hit_ratio` tụt mạnh, theo dõi `llm_ttft_seconds` (Time To First Token cho streaming), tỷ lệ `429` rate-limited và `402` budget-exceeded theo tenant.
  - **Redis & saturation metrics:** `redis_connected`, `redis_command_duration_seconds`, memory/CPU container (`container_memory_working_set_bytes`), và tích hợp Alertmanager gửi cảnh báo về Slack/PagerDuty kèm runbook xử lý.
