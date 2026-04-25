# AI_SYSTEM_FULL → forex-main Upgrade Patch

Gắn tư duy AI_SYSTEM_FULL vào forex-main mà không viết lại code gốc.

Files thêm mới:
- apps/api/app/core/ai_system_runtime.py
- apps/api/app/routers/operator.py
- services/trading-core/trading_core/runtime/governance.py

Files cần patch thủ công:
- apps/api/app/main.py: import/include operator router.
- services/trading-core/trading_core/runtime/bot_runtime.py: import TradingRuntimeGuard, tạo self._guard, validate market data / daily loss / signal / broker health trong tick loop.

Verify:
python -m compileall apps/api/app services/trading-core/trading_core
pytest -q apps/api/tests services/trading-core/tests
