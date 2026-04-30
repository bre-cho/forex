# Integrity Worker Runbook

## Mục tiêu

Integrity worker chạy kiểm tra nightly cho order ledger để phát hiện sớm các vi phạm bất biến giữa:

- orders projection
- order_state_transitions
- broker_execution_receipts
- submit_outbox
- reconciliation_queue_items

Khi có vi phạm mức critical, worker tự tạo incident loại `order_ledger_integrity_violation` để operator xử lý fail-closed.

## Thành phần liên quan

- Worker entrypoint: `apps/api/app/workers/order_ledger_integrity_worker_entrypoint.py`
- Nightly checker: `apps/api/app/workers/verify_order_ledger_integrity.py`
- Integrity service: `apps/api/app/services/order_ledger_integrity_service.py`
- Production service: `integrity-worker` trong `infra/docker/docker-compose.prod.yml`

## Biến môi trường

Biến dùng bởi integrity-worker:

- `INTEGRITY_RUN_HOUR_UTC`
  - Giờ UTC chạy nightly.
  - Mặc định: `0`
- `INTEGRITY_RUN_MINUTE_UTC`
  - Phút UTC chạy nightly.
  - Mặc định: `15`
- `INTEGRITY_RUN_ON_STARTUP`
  - Có chạy 1 lần ngay khi container start hay không.
  - Giá trị hợp lệ: `true|false` (`1|0`, `yes|no` cũng được map).
  - Mặc định: `true`

Biến hạ tầng bắt buộc:

- `DATABASE_URL`
- `REDIS_URL`
- `APP_ENV=production`

## Override lịch chạy

### Cách 1: Override qua .env

Thêm vào file `.env` dùng cho production compose:

```bash
INTEGRITY_RUN_HOUR_UTC=1
INTEGRITY_RUN_MINUTE_UTC=30
INTEGRITY_RUN_ON_STARTUP=true
```

Sau đó restart service:

```bash
docker compose -f infra/docker/docker-compose.prod.yml up -d integrity-worker
```

### Cách 2: Override trực tiếp khi chạy compose

```bash
INTEGRITY_RUN_HOUR_UTC=3 INTEGRITY_RUN_MINUTE_UTC=0 \
  docker compose -f infra/docker/docker-compose.prod.yml up -d integrity-worker
```

## Vận hành thường ngày

Kiểm tra service:

```bash
docker compose -f infra/docker/docker-compose.prod.yml ps integrity-worker
```

Theo dõi log:

```bash
docker compose -f infra/docker/docker-compose.prod.yml logs -f integrity-worker
```

Dấu hiệu hoạt động bình thường trong log:

- startup run report có `ok=true` hoặc `critical=0`
- có dòng next run in ... seconds
- nightly run report xuất hiện theo lịch UTC đã cấu hình

## Chạy thủ công một lần

Dùng khi cần verify sau deploy hoặc sau migration:

```bash
python -m app.workers.verify_order_ledger_integrity
```

Nếu chạy trong container api:

```bash
docker compose -f infra/docker/docker-compose.prod.yml exec api \
  python -m app.workers.verify_order_ledger_integrity
```

## Cách đọc incident khi invariant fail

Integrity checker tạo incident:

- `incident_type = order_ledger_integrity_violation`
- `severity = critical`
- `detail` chứa JSON issue với các field:
  - `code`
  - `bot_instance_id`
  - `idempotency_key`
  - `detail`

Các code thường gặp:

- `order_state_projection_mismatch`
- `filled_without_receipt`
- `unknown_without_reconciliation_queue`
- `submit_outbox_unknown_after_send_without_queue`
- `queue_item_without_attempt`

### Quy trình xử lý đề xuất

1. Xác nhận phạm vi ảnh hưởng theo `bot_instance_id` và `idempotency_key`.
2. Mở timeline/order-state-transitions/execution-receipts của bot để đối chiếu.
3. Nếu có UNKNOWN path, kiểm tra reconciliation queue và trigger reconcile/manual resolve.
4. Nếu mismatch projection, chạy lại projector hoặc sửa dữ liệu theo ledger source-of-truth.
5. Chỉ resolve incident sau khi rerun integrity checker và không còn critical issue cùng key.

## Playbook nhanh theo code

### submit_outbox_unknown_after_send_without_queue

- Triệu chứng: broker đã send nhưng không có queue unresolved.
- Hành động:
  1. Enqueue lại reconciliation item theo idempotency key.
  2. Chạy reconcile daemon hoặc manual resolve endpoint.
  3. Rerun integrity checker.

### filled_without_receipt

- Triệu chứng: order đã filled nhưng thiếu broker_execution_receipt.
- Hành động:
  1. Kiểm tra raw broker evidence.
  2. Backfill receipt bằng lifecycle event chuẩn.
  3. Đồng bộ projection và xác nhận transition cuối.

### order_state_projection_mismatch

- Triệu chứng: orders.current_state lệch với transition mới nhất.
- Hành động:
  1. Xác thực transition chain.
  2. Rebuild projection cho idempotency key bị lỗi.
  3. Rerun integrity checker.

## Sau deploy

Checklist bắt buộc:

1. Alembic đã lên head (bao gồm `0019_submit_outbox`, `0020_worker_heartbeats`).
2. `integrity-worker` đang chạy và có log startup run.
3. Run script verify compose:

```bash
bash scripts/ci/verify_prod_compose_no_legacy.sh
```

4. Chạy checker thủ công 1 lần và xác nhận không có critical issue.
