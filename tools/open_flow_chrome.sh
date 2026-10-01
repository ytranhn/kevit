#!/bin/bash
# Mở Chrome BÌNH THƯỜNG (không qua Playwright) với profile riêng + cổng debug.
# Đăng nhập thủ công 1 lần; tool sau đó gắn vào qua CDP localhost:9222.
DIR="$(cd "$(dirname "$0")/.." && pwd)/data/flow_profile"
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$DIR" \
  --no-first-run "https://labs.google/fx/tools/flow"
