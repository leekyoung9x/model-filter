# model-filter — Proxy lọc câu hỏi nguồn gốc model trước 9router

Lớp HTTP đứng trước [9router](https://github.com/decolua/9router) (OpenAI-compatible gateway).
Với combo `thtung-muse`, mọi câu hỏi về nguồn gốc model / nhà cung cấp / provider đều bị chặn
và trả câu trả lời cố định — không để lộ model thật phía sau. Câu hỏi bình thường và combo
khác được forward nguyên vẹn.

Chỉ dùng Python stdlib (`http.server` + `http.client`), không thêm dependency.

## Chạy

```bash
python3 proxy.py [--host 127.0.0.1] [--port 20130] [--upstream http://127.0.0.1:20127]
```

Docker (VPS 157, cùng network với 9router):

```bash
python3 /app/proxy.py --host 0.0.0.0 --port 20130 --upstream http://9router:20128
```

Compose mẫu (thêm vào `docker-compose.yml`, cùng networks với 9router):

```yaml
model-filter:
  image: python:3.14-alpine
  container_name: model-filter
  restart: always
  command: ["python3", "/app/proxy.py", "--host", "0.0.0.0", "--port", "20130", "--upstream", "http://9router:20128"]
  volumes: ["./model-filter/proxy.py:/app/proxy.py:ro"]
  networks: ["ai-net", "root_poki-net"]
```

Bẻ Caddy qua filter (1 dòng trong block domain router) + reload:

```
reverse_proxy model-filter:20130
```

Rollback: đổi lại `reverse_proxy 9router:20128` + reload.

Systemd (máy local, chưa enable): xem `model-filter-20130.service`.

## Hành vi

- `GET /health` → `{"ok": true}` (local, không forward).
- `GET /v1/models` → forward thẳng sang 9router.
- `POST /v1/chat/completions`:
  - Đọc `model` trong body. Chỉ filter khi model thuộc combo thtung-muse
    (`thtung-muse*` hoặc `muse/muse-spark-1.3-contributor`,
    `oc/muse-spark-1.3-contributor-free`, `oc/muse-spark-1.2-contributor-free`).
    Model khác → forward thẳng, không filter.
  - Gom text từ `messages[]` (role user/system; content string hoặc list
    content-parts) + `prompt` legacy, lowercase + NFC, so từ khóa VN/EN/vendor.
  - Trúng → trả ngay câu CANNED (không forward):
    `Xin lỗi, tôi không có thông tin về vấn đề này. Tôi chỉ có thể hỗ trợ trả lời các câu hỏi trong phạm vi công việc được giao.`
    Non-stream: `chat.completion`; stream: 1 chunk SSE + `data: [DONE]`.
  - Không trúng → forward nguyên vẹn (method/path/query/headers/body,
    giữ Authorization), pipe status+body về; stream relay chunk real-time
    (chunked). Timeout forward 120s.
- Log mỗi request ra stdout: timestamp, path, model, blocked, matched_keyword.
  KHÔNG log Authorization, KHÔNG log toàn bộ prompt.

## Test

```bash
bash test_filter.sh
```

Key 9router đọc từ sqlite lúc chạy, không hardcode. 12 checks: health, models,
5 câu dò nguồn gốc (VN có dấu/không dấu/EN) → CANNED, log blocked, model ngoài
combo forward, câu thường forward, stream 2 chiều. Exit != 0 nếu bất kỳ check nào fail.
