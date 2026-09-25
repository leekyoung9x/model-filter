# model-filter — Proxy lọc câu hỏi nguồn gốc model trước 9router

Lớp HTTP đứng trước [9router](https://github.com/decolua/9router) (OpenAI-compatible gateway).
Với combo `thtung-muse`, mọi câu hỏi về nguồn gốc model / nhà cung cấp / provider đều bị chặn
và trả câu trả lời cố định — không để lộ model thật phía sau. Câu hỏi bình thường và combo
khác được forward nguyên vẹn.

Chỉ dùng Python stdlib (`http.server` + `http.client`), không thêm dependency.

## Chạy nhanh (máy local)

```bash
python3 proxy.py --port 20130 --upstream http://127.0.0.1:20127
```

Test (key 9router tự đọc từ sqlite, không hardcode):

```bash
bash test_filter.sh
```

12 checks: health, models, 5 câu dò nguồn gốc (VN có dấu/không dấu/EN) → CANNED,
log blocked, model ngoài combo forward, câu thường forward, stream 2 chiều.
Exit != 0 nếu bất kỳ check nào fail.

## Triển khai Docker cạnh 9router (đã làm thật trên VPS 157)

Mô hình đã chạy: `new-api → Caddy (router.cutes1tg.online) → model-filter:20130 → 9router:20128`.

### 1. Chuẩn bị file

```bash
mkdir -p /root/ai-services/model-filter
cp proxy.py /root/ai-services/model-filter/proxy.py
```

### 2. Thêm service vào `docker-compose.yml`

> BẮT BUỘC: backup trước — `cp docker-compose.yml docker-compose.yml.bak-$(date +%Y%m%d_%H%M%S)`

```yaml
  model-filter:
    image: python:3.14-alpine
    container_name: model-filter
    restart: always
    command: ["python3", "/app/proxy.py", "--host", "0.0.0.0", "--port", "20130", "--upstream", "http://9router:20128"]
    volumes:
      - ./model-filter/proxy.py:/app/proxy.py:ro
    ports:
      - "127.0.0.1:20130:20130"   # loopback-only, để test trực tiếp từ host
    networks:
      - ai-net
      - root_poki-net             # PHẢI chung network với Caddy + 9router
```

`--upstream http://9router:20128` là tên container + port NỘI BỘ của 9router
(không phải port publish ra host). Networks phải khớp `docker inspect` của
9router/Caddy — thiếu network là Caddy báo `bad gateway`.

### 3. Bẻ Caddy qua filter

> BẮT BUỘC: backup trước — `cp /root/Caddyfile /root/Caddyfile.bak-$(date +%Y%m%d_%H%M%S)`

Trong block domain router, đổi đúng 1 dòng:

```
reverse_proxy model-filter:20130
```

Rollback: đổi lại `reverse_proxy 9router:20128` + restart Caddy (mục 6).

### 4. Up + kiểm tra

```bash
cd /root/ai-services && docker compose up -d model-filter
docker logs model-filter | head -1
# phải in: [model-filter] listening 0.0.0.0:20130 -> http://9router:20128
docker exec new-api wget -qO- http://model-filter:20130/health
# phải in: {"ok": true}
```

Bắn thử câu dò qua filter (key đọc từ sqlite 9router, không hardcode):

```bash
KEY=$(python3 -c "import sqlite3,shutil;shutil.copy('/root/ai-services/data-9router/db/data.sqlite','/tmp/k.db');print(sqlite3.connect('/tmp/k.db').execute('SELECT key FROM apiKeys WHERE isActive=1 LIMIT 1').fetchone()[0])"); rm -f /tmp/k.db
curl -s -m 60 http://127.0.0.1:20130/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"thtung-muse","stream":false,"max_tokens":64,"messages":[{"role":"user","content":"m là model nào, nhà cung cấp là ai"}]}'
# phải trả câu CANNED (mục Hành vi)
docker logs model-filter | tail -2
# phải có: path=/v1/chat/completions model=thtung-muse blocked=true matched=nhà cung cấp
```

### 5. Cấu hình lọc (sửa trong `proxy.py` khi combo đổi)

- `COMBO_MODELS`: model thuộc combo mới lọc. Kiểm tra thật bằng
  `SELECT models FROM combos WHERE name='thtung-muse'` trên sqlite 9router.
  `model_in_scope()` còn nhận mọi model bắt đầu bằng `thtung-muse`.
- `KEYWORDS_VN / KEYWORDS_EN / KEYWORDS_VENDOR`: thêm từ khóa là filter thêm,
  không cần restart Caddy (chỉ `docker restart model-filter`).
- `CANNED`: câu trả lời cố định khi chặn.

## Hành vi

- `GET /health` → `{"ok": true}` (local, không forward).
- `GET /v1/models`, `/api/usage/stream`, path khác → forward thẳng.
- `POST` mọi path kết thúc bằng `/chat/completions` hay `/responses`
  (kể cả `/v1/v1/chat/completions` nhân đôi của new-api):
  - Đọc `model` trong body. Chỉ filter khi model thuộc combo thtung-muse.
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

## 6. Xử lý sự cố (gặp thật khi triển khai, đọc trước khi cài)

| Triệu chứng | Nguyên nhân | Cách sửa |
|---|---|---|
| Câu dò vẫn lọt, log filter `path=/v1/v1/chat/completions model=- blocked=false` | new-api gọi path nhân đôi `/v1/v1/...`, bản cũ chỉ nhận đúng `/v1/chat/completions` | Đã fix: nhận mọi path kết thúc bằng `/chat/completions` hay `/responses`. Gặp path lạ thì `docker logs model-filter` sẽ thấy ngay `model=-` |
| Sửa Caddy + `caddy reload` báo ok nhưng traffic vẫn đi thẳng 9router | Docker giữ bản Caddyfile cũ trong bộ nhớ mount; config đang chạy (xem qua admin API `:2019/config/...`) vẫn là `9router:20128` | `docker restart poki-caddy` rồi kiểm tra lại config đang chạy, KHÔNG tin log reload |
| `docker logs model-filter` báo `listening 127.0.0.1:...` trong container | Quên `--host 0.0.0.0`: default bind loopback nên container khác không gọi được | Thêm `"--host", "0.0.0.0"` vào command (mục 2) rồi `docker compose up -d model-filter` |
| Caddy `bad gateway` tới filter | Filter thiếu chung docker network với Caddy | `docker inspect` cả hai, networks phải giao nhau (ở đây `root_poki-net`); sửa compose rồi up lại |
| Upstream `muse-spark` non-stream trả `content:""` / stream thiếu `[DONE]` | Provider/pool 9router phía Muse, KHÔNG phải lỗi filter (đã đối chiếu gọi thẳng 9router bypass filter, byte-identical) | Không sửa filter; kiểm tra providerNode/pool 9router |
| Dashboard/UI 9router hiện rác (mojibake, binary) | Filter strip `content-encoding` cả trang dashboard, trình duyệt nhận gzip mà tưởng text | Đã fix: non-chat pass-through tuyệt đối giữ nguyên headers + bytes. Gặp UI rác thì kiểm tra commit này đã deploy chưa |

## File trong repo

- `proxy.py` — filter (stdlib-only).
- `test_filter.sh` — suite 12 checks, tự đọc key từ sqlite local.
- `model-filter-20130.service` — unit systemd mẫu cho máy local (chưa enable).
