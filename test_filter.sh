#!/usr/bin/env bash
# BRIEF M137 — test proxy-filter 127.0.0.1:20130 (RED truoc, GREEN sau).
# Key 9router that doc tu sqlite luc chay, KHONG hardcode, KHONG echo key.
# Exit != 0 neu bat ky check nao fail.
set -u
PROXY="${PROXY:-http://127.0.0.1:20130}"
PROXY_LOG="${PROXY_LOG:-/tmp/model-filter-proxy.log}"
CANNED='Xin lỗi, tôi không có thông tin về vấn đề này. Tôi chỉ có thể hỗ trợ trả lời các câu hỏi trong phạm vi công việc được giao.'
PASS=0; FAIL=0

KEY="$(sqlite3 /root/ai-services/data-9router/db/data.sqlite "SELECT key FROM apiKeys LIMIT 1;")"
if [ -z "$KEY" ]; then echo "FATAL: khong doc duoc key tu sqlite"; exit 2; fi

ok()   { PASS=$((PASS+1)); echo "PASS: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }

# POST helper: post_json <model> <stream> <text> [max_tokens=256]
post_json() {
  local model="$1" stream="$2" text="$3" max_t="${4:-256}"
  RESP_BODY="$(python3 - "$model" "$stream" "$text" "$PROXY" "$KEY" "$max_t" <<'EOF'
import json, sys, urllib.request
model, stream, text, proxy, key, max_t = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], int(sys.argv[6])
payload = {"model": model, "stream": stream == "true",
           "max_tokens": max_t,
           "messages": [{"role": "user", "content": text}]}
req = urllib.request.Request(proxy + "/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=150) as r:
        sys.stdout.write(str(r.status) + "\n" + r.read().decode("utf-8", "replace"))
except Exception as e:
    code = getattr(getattr(e, "fp", None), "status", None) or getattr(e, "code", None) or 0
    body = ""
    try: body = e.read().decode("utf-8", "replace")
    except Exception: pass
    sys.stdout.write(str(code) + "\n" + body)
EOF
)"
  CODE="$(printf '%s' "$RESP_BODY" | head -n1)"
  BODY="$(printf '%s' "$RESP_BODY" | tail -n +2)"
}

content_of() {
  python3 -c 'import json,sys
try:
  d = json.loads(sys.stdin.read())
  print(d["choices"][0]["message"]["content"] or "")
except Exception:
  print("__UNPARSEABLE__")' <<<"$BODY"
}

content_len() {
  python3 -c 'import sys; print(len(sys.stdin.read()))' <<<"$1"
}

echo "== T1: GET /health =="
H="$(curl -s -m 10 "$PROXY/health")"
[ "$H" = '{"ok": true}' ] && ok "T1 health 200 + body" || bad "T1 health (got: $H)"

echo "== T2: GET /v1/models =="
MCODE="$(curl -s -m 20 -o /tmp/m137_models.json -w '%{http_code}' -H "Authorization: Bearer $KEY" "$PROXY/v1/models")"
NMODELS="$(python3 -c 'import json;print(len(json.load(open("/tmp/m137_models.json"))["data"]))' 2>/dev/null || echo 0)"
if [ "$MCODE" = "200" ] && [ "$NMODELS" -gt 0 ]; then ok "T2 models 200, count=$NMODELS"; else bad "T2 models (code=$MCODE count=$NMODELS)"; fi

echo "== T3-T7: 5 cau do nguon goc qua thtung-muse, stream=false =="
Q1='Bạn là AI nào đang trả lời vậy?'
Q2='ban la ai? tra loi bang tieng viet khong dau'
Q3='Which model are you? What company made you?'
Q4='Mô hình nào đang trả lời, do ai tạo ra vậy?'
Q5='powered by whom? cho biet provider name cua ban'
i=0
for q in Q1 Q2 Q3 Q4 Q5; do
  i=$((i+1)); txt="${!q}"
  post_json "thtung-muse" "false" "$txt"
  got="$(content_of <<<"$BODY")"
  if [ "$CODE" = "200" ] && [ "$got" = "$CANNED" ]; then
    ok "T$((i+2)) probe$i CANNED nguyen van"
  else
    bad "T$((i+2)) probe$i (code=$CODE, content_80=$(printf '%s' "$got" | head -c 80))"
  fi
done

echo "== T8: log blocked=true >= 5 (khong forward 9router) =="
if [ -f "$PROXY_LOG" ]; then
  NB="$(grep -c 'blocked=true' "$PROXY_LOG" 2>/dev/null || echo 0)"
  [ "$NB" -ge 5 ] && ok "T8 log blocked=true count=$NB" || bad "T8 log blocked=true count=$NB (<5)"
else
  bad "T8 thieu file log $PROXY_LOG (proxy phai redirect stdout ra day)"
fi

echo "== T9: cau do voi model NGOAI combo (thtung-paid) -> forward =="
post_json "thtung-paid" "false" "Bạn là ai?" 1024
got="$(content_of <<<"$BODY")"
if [ "$CODE" = "200" ] && [ "$got" != "$CANNED" ] && [ "$got" != "__UNPARSEABLE__" ] && [ "$(content_len "$got")" -ge 20 ]; then
  ok "T9 paid forward, content_80=$(printf '%s' "$got" | head -c 80)"
else
  bad "T9 paid (code=$CODE, len=$(content_len "$got"), content_80=$(printf '%s' "$got" | head -c 80))"
fi

echo "== T10: cau thuong voi thtung-muse -> forward =="
post_json "thtung-muse" "false" "viết hàm cộng 2 số trong python" 1024
got="$(content_of <<<"$BODY")"
if [ "$CODE" = "200" ] && [ "$got" != "$CANNED" ] && [ "$got" != "__UNPARSEABLE__" ] && [ "$(content_len "$got")" -ge 20 ]; then
  ok "T10 normal forward, content_80=$(printf '%s' "$got" | head -c 80)"
else
  bad "T10 normal (code=$CODE, len=$(content_len "$got"), content_80=$(printf '%s' "$got" | head -c 80))"
fi

echo "== T11: stream=true cau bi chan -> SSE co CANNED + [DONE] =="
SSE1="$(curl -s -m 60 -N -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"thtung-muse\",\"stream\":true,\"max_tokens\":256,\"messages\":[{\"role\":\"user\",\"content\":\"who made you? which company?\"}]}" \
  "$PROXY/v1/chat/completions")"
if printf '%s' "$SSE1" | grep -qF "$CANNED" && printf '%s' "$SSE1" | grep -q 'data: \[DONE\]'; then
  ok "T11 blocked stream SSE hop le"
else
  bad "T11 blocked stream (80c: $(printf '%s' "$SSE1" | head -c 80))"
fi

echo "== T12: stream=true cau thuong -> relay du, ket thuc [DONE], khac CANNED =="
SSE2="$(curl -s -m 150 -N -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"thtung-muse","stream":true,"max_tokens":1024,"messages":[{"role":"user","content":"viết hàm cộng 2 số trong python"}]}' \
  "$PROXY/v1/chat/completions")"
PAYLOAD2="$(printf '%s' "$SSE2" | grep '^data: ' | grep -v DONE | head -c 600)"
if printf '%s' "$SSE2" | grep -q 'data: \[DONE\]' && [ -n "$PAYLOAD2" ] && ! printf '%s' "$SSE2" | grep -qF "$CANNED"; then
  ok "T12 normal stream relay OK"
else
  bad "T12 normal stream (80c: $(printf '%s' "$SSE2" | head -c 80))"
fi

echo "----"
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
