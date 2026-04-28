"""
Robot Forex — Bang dieu khien Streamlit
Bang dieu khien robot giao dich 6 trang, goi du lieu tu FastAPI backend.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

# ── Config ─────────────────────────────────────────────────────────────── #

BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
POLL_INTERVAL = 5   # seconds between auto-refresh

st.set_page_config(
    page_title="Bang dieu khien Robot Forex",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── API helpers ────────────────────────────────────────────────────────── #

def api_get(path: str, default: Any = None) -> Any:
    try:
        r = requests.get(f"{BACKEND}{path}", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return default


def api_post(path: str, data: Optional[dict] = None) -> Any:
    try:
        r = requests.post(f"{BACKEND}{path}", json=data or {}, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Loi API: {e}")
        return None


# ── Custom CSS ─────────────────────────────────────────────────────────── #

st.markdown("""
<style>
.metric-card {
    background: #1e2130;
    border-radius: 10px;
    padding: 16px 20px;
    text-align: center;
    border: 1px solid #2d3250;
}
.metric-card .label { font-size: 0.78rem; color: #8b95b0; text-transform: uppercase; letter-spacing: 0.05em; }
.metric-card .value { font-size: 1.6rem; font-weight: 700; margin-top: 4px; }
.bull { color: #00e676; }
.bear { color: #ff5252; }
.sideways { color: #ffd740; }
.subwave { color: #ff9100; }
.running-badge { background: #00c853; color: white; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85rem; }
.stopped-badge { background: #c62828; color: white; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85rem; }
.cooldown-badge { background: #e65100; color: white; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ────────────────────────────────────────────────────────────── #

with st.sidebar:
    st.markdown("## 🤖 Robot Forex")
    st.markdown("---")

    status = api_get("/api/status", {})
    running = status.get("running", False)

    # ── Daily Lock Status ─────────────────────────────────────────────── #
    daily_lock = api_get("/api/risk/daily_lock", {})
    lock_active = daily_lock.get("locked", False)
    profit_locked = daily_lock.get("profit_locked", False)
    loss_locked = daily_lock.get("loss_locked", False)

    if profit_locked:
        st.markdown('<div style="background:#1b5e20;border-radius:8px;padding:8px 12px;margin-bottom:8px">🏆 <b>Da dat muc tieu loi nhuan ngay!</b><br><small>Robot da tu dung. Dat lai de tiep tuc.</small></div>', unsafe_allow_html=True)
    elif loss_locked:
        st.markdown('<div style="background:#b71c1c;border-radius:8px;padding:8px 12px;margin-bottom:8px">🛑 <b>Da cham gioi han lo ngay!</b><br><small>Robot da tu dung. Dat lai de tiep tuc.</small></div>', unsafe_allow_html=True)

    if running:
        st.markdown('<span class="running-badge">● DANG CHAY</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="stopped-badge">● DA DUNG</span>', unsafe_allow_html=True)

    st.markdown("")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶ Bat dau", use_container_width=True, type="primary", disabled=running or lock_active):
            result = api_post("/api/robot/start")
            if result:
                st.success("Da bat dau!")
                st.rerun()
    with col2:
        if st.button("■ Dung", use_container_width=True, type="secondary", disabled=not running):
            result = api_post("/api/robot/stop")
            if result:
                st.warning("Da dung")
                st.rerun()

    # Reset daily lock button
    if lock_active:
        if st.button("🔓 Dat lai khoa ngay", use_container_width=True, type="secondary"):
            result = api_post("/api/robot/reset_daily_lock")
            if result:
                st.success("Da dat lai khoa ngay! Ban co the khoi dong lai robot.")
                st.rerun()

    st.markdown("---")
    wave = status.get("wave_state", "SIDEWAYS")
    sub = status.get("sub_wave")
    conf = status.get("confidence", 0.0)

    wave_cls = "bull" if "BULL" in wave else ("bear" if "BEAR" in wave else "sideways")
    st.markdown(f'<div style="text-align:center"><span class="{wave_cls}" style="font-size:1.2rem;font-weight:700">{wave}</span></div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<div style="text-align:center"><span class="subwave">⚠ Song phu: {sub}</span></div>', unsafe_allow_html=True)
    st.progress(conf, text=f"Do tin cay: {conf:.0%}")

    st.markdown("---")
    bal = status.get("balance", 10000)
    eq = status.get("equity", 10000)
    pnl = status.get("total_pnl", 0)
    daily_pnl_sidebar = daily_lock.get("daily_pnl", 0.0)
    profit_target_sb = daily_lock.get("daily_profit_target", 0.0)
    loss_limit_sb = daily_lock.get("daily_loss_limit", 0.0)

    st.metric("So du", f"${bal:,.2f}")
    st.metric("Von chu so huu", f"${eq:,.2f}", delta=f"{eq - bal:+.2f}")
    st.metric("Tong Lai/Lo", f"${pnl:,.2f}", delta=f"{pnl:+.2f}")

    # Daily PnL progress bar
    st.markdown("**Lai/Lo trong ngay**")
    st.markdown(f"${daily_pnl_sidebar:+.2f}")
    if profit_target_sb > 0:
        pct = min(max(daily_pnl_sidebar / profit_target_sb, 0), 1)
        st.progress(pct, text=f"Muc tieu loi nhuan: {pct:.0%} cua ${profit_target_sb:.0f}")
    if loss_limit_sb > 0:
        loss_pct = min(max(-daily_pnl_sidebar / loss_limit_sb, 0), 1)
        if loss_pct > 0:
            st.progress(loss_pct, text=f"Gioi han lo: {loss_pct:.0%} cua ${loss_limit_sb:.0f}")

    st.markdown("---")
    auto_refresh = st.checkbox("Tu dong lam moi (5s)", value=True)
    if st.button("🔄 Lam moi ngay"):
        st.rerun()

    if auto_refresh:
        time.sleep(POLL_INTERVAL)
        st.rerun()

# ── Navigation ─────────────────────────────────────────────────────────── #

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Tong quan",
    "🌊 Phan tich song",
    "📋 Hang doi tin hieu",
    "⚙️ Cai dat",
    "📜 Lich su giao dich",
    "🤖 AI va dieu khien",
])


# ══════════════════════════════════════════════════════════════════════════ #
#  PAGE 1 — DASHBOARD                                                       #
# ══════════════════════════════════════════════════════════════════════════ #

with tab1:
    st.markdown("## 📊 Tong quan")

    status = api_get("/api/status", {})
    risk = api_get("/api/risk/metrics", {})
    candles = api_get("/api/candles?limit=100", [])
    daily_lock_d = api_get("/api/risk/daily_lock", {})

    # ── Daily Lock / Drawdown Alerts ────────────────────────────────── #
    if daily_lock_d.get("profit_locked"):
        st.success(
            f"🏆 **Da dat muc tieu loi nhuan ngay!** "
            f"Lai/Lo ngay: ${daily_lock_d.get('daily_pnl', 0):+.2f} "
            f"(muc tieu: ${daily_lock_d.get('daily_profit_target', 0):.2f}) — "
            "Robot **da tu dung**. Dung thanh ben de dat lai va khoi dong lai."
        )
    elif daily_lock_d.get("loss_locked"):
        st.error(
            f"🛑 **Da cham gioi han lo ngay!** "
            f"Lai/Lo ngay: ${daily_lock_d.get('daily_pnl', 0):+.2f} "
            f"(gioi han: -${daily_lock_d.get('daily_loss_limit', 0):.2f}) — "
            "Robot **da tu dung**. Dung thanh ben de dat lai va khoi dong lai."
        )

    # Top metrics row
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        wave = status.get("wave_state", "—")
        wcolor = "🟢" if "BULL" in wave else ("🔴" if "BEAR" in wave else "🟡")
        st.metric("Song chinh", f"{wcolor} {wave}")
    with c2:
        st.metric("Do tin cay", f"{status.get('confidence', 0):.0%}")
    with c3:
        st.metric("Ty le thang", f"{status.get('win_rate', 0):.1f}%")
    with c4:
        st.metric("He so loi nhuan", f"{status.get('profit_factor', 0):.2f}")
    with c5:
        st.metric("Lenh mo", status.get("open_trades", 0))
    with c6:
        st.metric("Tong lenh", status.get("total_trades", 0))

    st.markdown("---")

    # Sub-wave warning
    sub_wave = status.get("sub_wave")
    if sub_wave:
        st.warning(f"⚠️ **Phat hien song phu: {sub_wave}** — Tam dung giao dich cho den khi song chinh tiep tuc")

    # Drawdown alert
    if risk.get("dd_triggered"):
        st.error("🚨 **Bao ve drawdown da kich hoat** — Tat ca giao dich tam dung")
    elif risk.get("daily_profit_locked"):
        st.success(f"🏆 **Khoa loi nhuan ngay** — {risk.get('lock_reason', '')}")
    elif risk.get("daily_loss_locked"):
        st.error(f"🛑 **Khoa lo ngay** — {risk.get('lock_reason', '')}")

    col_left, col_right = st.columns([2, 1])

    with col_left:
        # Equity curve from closed trades
        trades_data = api_get("/api/trades?page_size=200", {})
        trades = trades_data.get("trades", []) if trades_data else []
        closed = [t for t in trades if t.get("status") == "CLOSED"]

        if closed:
            df_trades = pd.DataFrame(closed)
            df_trades["open_time"] = pd.to_datetime(df_trades["open_time"], unit="s")
            df_trades = df_trades.sort_values("open_time")
            df_trades["cumulative_pnl"] = df_trades["pnl"].cumsum()

            fig_equity = go.Figure()
            colors = ["#00e676" if p >= 0 else "#ff5252" for p in df_trades["cumulative_pnl"]]
            fig_equity.add_trace(go.Scatter(
                x=df_trades["open_time"],
                y=df_trades["cumulative_pnl"],
                mode="lines+markers",
                name="Lai/Lo luy ke",
                line=dict(color="#00e676", width=2),
                fill="tozeroy",
                fillcolor="rgba(0,230,118,0.1)",
            ))
            fig_equity.update_layout(
                title="Duong cong Lai/Lo luy ke",
                xaxis_title="Thoi gian",
                yaxis_title="Lai/Lo ($)",
                height=350,
                paper_bgcolor="#0e1117",
                plot_bgcolor="#0e1117",
                font=dict(color="#fafafa"),
                xaxis=dict(gridcolor="#1e2130"),
                yaxis=dict(gridcolor="#1e2130"),
            )
            st.plotly_chart(fig_equity, use_container_width=True)
        else:
            st.info("📈 Duong cong von se hien thi sau khi co lenh dong.")

        # Price mini-chart
        if candles:
            df_c = pd.DataFrame(candles)
            df_c["dt"] = pd.to_datetime(df_c["timestamp"], unit="s")
            df_c = df_c.tail(50)
            fig_price = go.Figure(data=[go.Candlestick(
                x=df_c["dt"],
                open=df_c["open"],
                high=df_c["high"],
                low=df_c["low"],
                close=df_c["close"],
                name="Gia",
                increasing_line_color="#00e676",
                decreasing_line_color="#ff5252",
            )])
            fig_price.update_layout(
                title="Dien bien gia gan day (50 nen cuoi)",
                height=280,
                paper_bgcolor="#0e1117",
                plot_bgcolor="#0e1117",
                font=dict(color="#fafafa"),
                xaxis=dict(gridcolor="#1e2130", rangeslider=dict(visible=False)),
                yaxis=dict(gridcolor="#1e2130"),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_price, use_container_width=True)

    with col_right:
        # Risk panel
        st.markdown("### Chi so rui ro")
        balance = risk.get("balance", 0)
        equity_r = risk.get("equity", balance)
        peak = risk.get("peak_equity", balance)
        dd_pct = (peak - equity_r) / peak * 100 if peak > 0 else 0.0

        st.metric("So du", f"${balance:,.2f}")
        st.metric("Von chu so huu", f"${equity_r:,.2f}")
        st.metric("Lai/Lo ngay", f"${risk.get('daily_pnl', 0):+,.2f}")
        st.metric("Dinh von", f"${peak:,.2f}")

        dd_color = "normal" if dd_pct < 5 else ("off" if dd_pct < 15 else "inverse")
        st.metric("DD hien tai", f"{dd_pct:.2f}%", delta=f"-{dd_pct:.2f}%", delta_color="inverse")
        st.metric("Buoc Martingale", risk.get("martingale_step", 0))
        st.metric("So lenh lo lien tiep", risk.get("consecutive_losses", 0))
        st.metric("Do chenh gia", f"{risk.get('spread', 0):.1f} pips")

        # Open trades
        open_trades = api_get("/api/trades/open", [])
        if open_trades:
            st.markdown("### Lenh mo")
            df_open = pd.DataFrame(open_trades)
            st.dataframe(
                df_open[["trade_id", "symbol", "direction", "lot_size", "entry_price", "pnl"]],
                use_container_width=True,
                hide_index=True,
            )


# ══════════════════════════════════════════════════════════════════════════ #
#  PAGE 2 — WAVE ANALYSIS                                                   #
# ══════════════════════════════════════════════════════════════════════════ #

with tab2:
    st.markdown("## 🌊 Phan tich song")

    wave_data = api_get("/api/wave/analysis", {})
    candles = api_get("/api/candles?limit=200", [])

    if not wave_data:
        st.error("Khong the tai phan tich song. He thong may chu co dang chay khong?")
    else:
        # State banner
        main_wave = wave_data.get("main_wave", "SIDEWAYS")
        sub_wave = wave_data.get("sub_wave")
        conf = wave_data.get("confidence", 0.0)
        sideways = wave_data.get("sideways_detected", False)

        bcol1, bcol2, bcol3, bcol4 = st.columns(4)
        with bcol1:
            color = "🟢" if "BULL" in main_wave else ("🔴" if "BEAR" in main_wave else "🟡")
            st.metric("Song chinh", f"{color} {main_wave}")
        with bcol2:
            sub_label = sub_wave if sub_wave else "Khong co"
            st.metric("Song phu", f"{'⚠️ ' if sub_wave else ''}{sub_label}")
        with bcol3:
            st.metric("Do tin cay", f"{conf:.0%}")
        with bcol4:
            st.metric("Di ngang", "Co 🟡" if sideways else "Khong ✅")

        can_buy = wave_data.get("can_trade_buy", False)
        can_sell = wave_data.get("can_trade_sell", False)
        trade_status = []
        if can_buy:
            trade_status.append("✅ Cho phep tin hieu MUA")
        if can_sell:
            trade_status.append("✅ Cho phep tin hieu BAN")
        if not can_buy and not can_sell:
            trade_status.append("🚫 Tam dung giao dich (song phu hoac di ngang)")

        for ts in trade_status:
            if "✅" in ts:
                st.success(ts)
            else:
                st.warning(ts)

        st.markdown(f"**Phan tich:** {wave_data.get('description', '')}")

        if candles:
            df_c = pd.DataFrame(candles).tail(150)
            df_c["dt"] = pd.to_datetime(df_c["timestamp"], unit="s")

            htf_fast = wave_data.get("htf_ema_fast", 0)
            htf_slow = wave_data.get("htf_ema_slow", 0)
            ltf_fast = wave_data.get("ltf_ema_fast", 0)
            ltf_slow = wave_data.get("ltf_ema_slow", 0)
            atr = wave_data.get("atr", 0)

            # Compute EMA series from candle data
            close_series = df_c["close"]
            ema_htf_fast = close_series.ewm(span=21, adjust=False).mean()
            ema_htf_slow = close_series.ewm(span=50, adjust=False).mean()
            ema_ltf_fast = close_series.ewm(span=8, adjust=False).mean()
            ema_ltf_slow = close_series.ewm(span=21, adjust=False).mean()

            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                row_heights=[0.75, 0.25],
                vertical_spacing=0.03,
            )

            # Candlestick
            fig.add_trace(go.Candlestick(
                x=df_c["dt"],
                open=df_c["open"],
                high=df_c["high"],
                low=df_c["low"],
                close=df_c["close"],
                name="Gia",
                increasing_line_color="#00e676",
                decreasing_line_color="#ff5252",
            ), row=1, col=1)

            # EMAs
            fig.add_trace(go.Scatter(x=df_c["dt"], y=ema_htf_fast, name="EMA nhanh HTF(21)",
                                      line=dict(color="#40c4ff", width=1.5)), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_c["dt"], y=ema_htf_slow, name="EMA cham HTF(50)",
                                      line=dict(color="#ff6d00", width=1.5)), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_c["dt"], y=ema_ltf_fast, name="EMA nhanh LTF(8)",
                                      line=dict(color="#b39ddb", width=1, dash="dot")), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_c["dt"], y=ema_ltf_slow, name="EMA cham LTF(21)",
                                      line=dict(color="#f48fb1", width=1, dash="dot")), row=1, col=1)

            # Fractal swing points
            swing_highs = wave_data.get("swing_highs", [])
            swing_lows = wave_data.get("swing_lows", [])

            if swing_highs:
                sh_prices = [p["price"] for p in swing_highs]
                n = len(df_c)
                sh_xs = [df_c["dt"].iloc[max(0, min(int(p["index"]) % n, n - 1))] for p in swing_highs]
                fig.add_trace(go.Scatter(
                    x=sh_xs, y=sh_prices,
                    mode="markers",
                    name="Dinh fractal",
                    marker=dict(symbol="triangle-up", size=10, color="#ff5252"),
                ), row=1, col=1)

            if swing_lows:
                sl_prices = [p["price"] for p in swing_lows]
                n = len(df_c)
                sl_xs = [df_c["dt"].iloc[max(0, min(int(p["index"]) % n, n - 1))] for p in swing_lows]
                fig.add_trace(go.Scatter(
                    x=sl_xs, y=sl_prices,
                    mode="markers",
                    name="Day fractal",
                    marker=dict(symbol="triangle-down", size=10, color="#00e676"),
                ), row=1, col=1)

            # Volume bar
            fig.add_trace(go.Bar(
                x=df_c["dt"], y=df_c["volume"],
                name="Khoi luong",
                marker_color="rgba(128,128,200,0.5)",
            ), row=2, col=1)

            fig.update_layout(
                title=f"Bieu do phan tich song — {main_wave}",
                height=650,
                paper_bgcolor="#0e1117",
                plot_bgcolor="#0e1117",
                font=dict(color="#fafafa"),
                xaxis=dict(gridcolor="#1e2130", rangeslider=dict(visible=False)),
                yaxis=dict(gridcolor="#1e2130"),
                xaxis2=dict(gridcolor="#1e2130"),
                yaxis2=dict(gridcolor="#1e2130"),
                legend=dict(bgcolor="rgba(30,33,48,0.8)"),
            )
            st.plotly_chart(fig, use_container_width=True)

        # EMA values
        st.markdown("### Gia tri EMA hien tai")
        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("EMA nhanh HTF(21)", f"{wave_data.get('htf_ema_fast', 0):.5f}")
        col_b.metric("EMA cham HTF(50)", f"{wave_data.get('htf_ema_slow', 0):.5f}")
        col_c.metric("EMA nhanh LTF(8)", f"{wave_data.get('ltf_ema_fast', 0):.5f}")
        col_d.metric("EMA cham LTF(21)", f"{wave_data.get('ltf_ema_slow', 0):.5f}")
        st.metric("ATR(14)", f"{wave_data.get('atr', 0):.5f}")


# ══════════════════════════════════════════════════════════════════════════ #
#  PAGE 3 — SIGNAL QUEUE                                                    #
# ══════════════════════════════════════════════════════════════════════════ #

with tab3:
    st.markdown("## 📋 Hang doi tin hieu")

    queue = api_get("/api/queue/status", {})
    if not queue:
        st.error("Khong the tai trang thai hang doi.")
    else:
        state = queue.get("state", "IDLE")
        authority = queue.get("authority", "NORMAL")
        cooldown_until = queue.get("cooldown_until", 0)

        # State indicators
        s_col1, s_col2, s_col3, s_col4 = st.columns(4)
        with s_col1:
            state_emoji = {"IDLE": "⚫", "MONITORING": "🔵", "COOLDOWN": "🟠", "RESTRICTED": "🔴"}.get(state, "⚪")
            state_label = {
                "IDLE": "Nghi",
                "MONITORING": "Giam sat",
                "COOLDOWN": "Hoi chieu",
                "RESTRICTED": "Bi gioi han",
            }.get(state, state)
            st.metric("Trang thai dieu phoi", f"{state_emoji} {state_label}")
        with s_col2:
            auth_emoji = {"BLOCKED": "🚫", "RESTRICTED": "⚠️", "NORMAL": "✅", "PRIORITY": "⭐"}.get(authority, "❓")
            authority_label = {
                "BLOCKED": "Bi chan",
                "RESTRICTED": "Gioi han",
                "NORMAL": "Binh thuong",
                "PRIORITY": "Uu tien",
            }.get(authority, authority)
            st.metric("Muc uu tien", f"{auth_emoji} {authority_label}")
        with s_col3:
            st.metric("Do sau hang doi", queue.get("queue_depth", 0))
        with s_col4:
            if cooldown_until > time.time():
                remaining = max(0, int(cooldown_until - time.time()))
                st.metric("Thoi gian hoi chieu con lai", f"{remaining}s")
            else:
                st.metric("Hoi chieu", "Khong co ✅")

        if state == "COOLDOWN":
            st.warning(f"⏱ Dang hoi chieu sau khi lo. Se tiep tuc sau {max(0, int(cooldown_until - time.time()))} giay.")

        st.markdown("---")

        # Metrics row
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Tong tin hieu da vao hang doi", queue.get("signals_queued", 0))
        m2.metric("Tin hieu da thuc thi", queue.get("signals_executed", 0))
        m3.metric("Tin hieu bi tu choi", queue.get("signals_rejected", 0))
        m4.metric("Tin hieu het han", queue.get("signals_expired", 0))

        # Execution rate
        total = queue.get("signals_queued", 1) or 1
        exec_rate = queue.get("signals_executed", 0) / total * 100
        st.progress(exec_rate / 100, text=f"Ty le thuc thi: {exec_rate:.1f}%")

        # Recent signal history
        recent = queue.get("recent_signals", [])
        if recent:
            st.markdown("### Lich su tin hieu gan day")
            df_sig = pd.DataFrame(recent)
            if "timestamp" in df_sig.columns:
                df_sig["time"] = pd.to_datetime(df_sig["timestamp"], unit="s").dt.strftime("%H:%M:%S")
            status_colors = {
                "EXECUTED": "background-color: rgba(0,200,83,0.2)",
                "REJECTED": "background-color: rgba(255,82,82,0.2)",
                "QUEUED": "background-color: rgba(64,196,255,0.2)",
                "EXPIRED": "background-color: rgba(255,145,0,0.2)",
            }

            def color_status(val):
                return status_colors.get(val, "")

            display_cols = ["time", "signal_id", "symbol", "direction", "status", "reason"] if "time" in df_sig.columns else df_sig.columns.tolist()
            available_cols = [c for c in display_cols if c in df_sig.columns]

            if available_cols:
                styled = df_sig[available_cols].style.applymap(color_status, subset=["status"] if "status" in available_cols else [])
                st.dataframe(styled, use_container_width=True, hide_index=True)
        else:
            st.info("Chua co lich su tin hieu. Hay bat dau robot de tao tin hieu.")

        # Visual queue depth gauge
        st.markdown("### Giam sat tai")
        max_q = api_get("/api/settings", {}).get("max_queue_size", 10)
        depth = queue.get("queue_depth", 0)
        load_pct = depth / max_q if max_q > 0 else 0

        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=depth,
            title={"text": "Do sau hang doi", "font": {"color": "#fafafa"}},
            gauge={
                "axis": {"range": [0, max_q], "tickcolor": "#fafafa"},
                "bar": {"color": "#40c4ff"},
                "bgcolor": "#1e2130",
                "steps": [
                    {"range": [0, max_q * 0.5], "color": "rgba(0,230,118,0.2)"},
                    {"range": [max_q * 0.5, max_q * 0.8], "color": "rgba(255,215,0,0.2)"},
                    {"range": [max_q * 0.8, max_q], "color": "rgba(255,82,82,0.2)"},
                ],
                "threshold": {"line": {"color": "#ff5252", "width": 3}, "thickness": 0.75, "value": max_q * 0.9},
            },
        ))
        fig_gauge.update_layout(
            height=250,
            paper_bgcolor="#0e1117",
            font=dict(color="#fafafa"),
        )
        st.plotly_chart(fig_gauge, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════ #
#  PAGE 4 — SETTINGS                                                        #
# ══════════════════════════════════════════════════════════════════════════ #

with tab4:
    st.markdown("## ⚙️ Cai dat robot")

    current = api_get("/api/settings", {})
    if not current:
        st.error("Khong the tai cai dat.")
    else:
        with st.form("settings_form"):
            # ── Basic Setup ──────────────────────────────────────────── #
            with st.expander("🔧 Thiet lap co ban", expanded=True):
                bc1, bc2, bc3 = st.columns(3)
                username = bc1.text_input("Ten dang nhap", value=current.get("username", "Trader"))
                magic = bc2.number_input("Ma Magic", value=current.get("magic_number", 100001), min_value=1)
                symbol = bc3.text_input("Cap giao dich", value=current.get("symbol", "EURUSD"))
                tf1, tf2 = st.columns(2)
                timeframe = tf1.selectbox("Khung thoi gian", ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
                                           index=["M1", "M5", "M15", "M30", "H1", "H4", "D1"].index(current.get("timeframe", "M5")))
                htf_tf = tf2.selectbox("Khung thoi gian HTF", ["M15", "M30", "H1", "H4", "D1"],
                                        index=["M15", "M30", "H1", "H4", "D1"].index(current.get("htf_timeframe", "H1")))

            # ── Risk & Position Sizing ───────────────────────────────── #
            with st.expander("💰 Rui ro va kich thuoc vi the"):
                r1, r2, r3 = st.columns(3)
                lot_mode = r1.selectbox("Che do lot", ["STATIC", "DYNAMIC_PERCENT", "LOT_PER_X_BALANCE"],
                                         index=["STATIC", "DYNAMIC_PERCENT", "LOT_PER_X_BALANCE"].index(current.get("lot_mode", "STATIC")))
                lot_value = r2.number_input("Gia tri lot", value=float(current.get("lot_value", 0.01)),
                                             min_value=0.001, max_value=100.0, format="%.3f")
                pip_value = r3.number_input("Gia tri pip/lot ($)", value=float(current.get("pip_value_per_lot", 10.0)))
                rr1, rr2 = st.columns(2)
                min_lot = rr1.number_input("Lot toi thieu", value=float(current.get("min_lot", 0.01)), format="%.2f")
                max_lot = rr2.number_input("Lot toi da", value=float(current.get("max_lot", 10.0)), format="%.2f")

                st.markdown("**Martingale**")
                mg = current.get("martingale", {})
                m1c, m2c, m3c = st.columns(3)
                mg_enabled = m1c.checkbox("Bat Martingale", value=mg.get("enabled", False))
                mg_mult = m2c.number_input("He so nhan", value=float(mg.get("multiplier", 2.0)), min_value=1.0, max_value=10.0)
                mg_steps = m3c.number_input("So buoc toi da", value=int(mg.get("max_steps", 4)), min_value=1, max_value=10)

            # ── SL / TP ──────────────────────────────────────────────── #
            with st.expander("🎯 Cat lo va chot loi"):
                sl_modes = ["POINTS", "ATR", "RANGE_SIZE", "PREV_CANDLE_POINTS", "PREV_CANDLE_ATR",
                            "LAST_SWING_POINTS", "LAST_SWING_ATR", "RANGE_OPPOSITE_POINTS", "RANGE_OPPOSITE_ATR"]
                tp_modes = ["SL_RATIO", "ATR", "POINTS"]
                s1, s2 = st.columns(2)
                sl_mode = s1.selectbox("Che do SL", sl_modes,
                                        index=sl_modes.index(current.get("sl_mode", "POINTS")))
                sl_value = s1.number_input("Gia tri SL", value=float(current.get("sl_value", 200.0)), min_value=1.0)
                tp_mode = s2.selectbox("Che do TP", tp_modes,
                                        index=tp_modes.index(current.get("tp_mode", "SL_RATIO")))
                tp_value = s2.number_input("Gia tri TP", value=float(current.get("tp_value", 2.0)), min_value=0.1)

            # ── Sessions & Time ──────────────────────────────────────── #
            with st.expander("🕐 Phien giao dich va thoi gian"):
                ss1, ss2, ss3 = st.columns(3)
                sessions = ["AMERICAN", "NYSE", "EUROPEAN", "LONDON", "ASIAN", "CUSTOM", "ALL_DAY"]
                session = ss1.selectbox("Phien", sessions,
                                         index=sessions.index(current.get("session", "LONDON")))
                dst_modes = ["NO_DST", "NORTH_AMERICA", "EUROPE"]
                dst_mode = ss2.selectbox("Che do DST", dst_modes,
                                          index=dst_modes.index(current.get("dst_mode", "NO_DST")))
                gmt_offset = ss3.number_input("Do lech GMT", value=float(current.get("gmt_offset", 0.0)),
                                               min_value=-12.0, max_value=14.0, step=0.5)
                monitoring_minutes = st.number_input("So phut giam sat (chu ky dao dong)",
                                                      value=int(current.get("monitoring_minutes", 60)),
                                                      min_value=5, max_value=240)

            # ── Entry Logic ──────────────────────────────────────────── #
            with st.expander("📐 Logic vao lenh"):
                entry_modes = ["BREAKOUT", "INSTANT_BREAKOUT", "RETRACE", "INSTANT_RETRACE",
                               "RETEST_SAME", "RETEST_OPPOSITE", "RETEST_LEVEL_X"]
                e1, e2 = st.columns(2)
                entry_mode = e1.selectbox("Che do vao lenh", entry_modes,
                                           index=entry_modes.index(current.get("entry_mode", "BREAKOUT")))
                retrace_mult = e1.number_input("He so ATR cho retrace", value=float(current.get("retrace_atr_mult", 0.5)))
                min_body_atr = e2.number_input("Than nen ATR toi thieu", value=float(current.get("min_body_atr", 0.3)))
                retest_lvl = e2.number_input("Muc retest X (0-1)", value=float(current.get("retest_level_x", 0.5)),
                                              min_value=0.0, max_value=1.0)

            # ── Filters ──────────────────────────────────────────────── #
            with st.expander("🔍 Bo loc"):
                f1, f2 = st.columns(2)
                ema_filter = f1.checkbox("Bo loc EMA", value=current.get("ema_filter_enabled", True))
                ema_fast = f1.number_input("Chu ky EMA nhanh", value=int(current.get("ema_fast", 21)))
                ema_slow = f1.number_input("Chu ky EMA cham", value=int(current.get("ema_slow", 50)))
                sr_filter = f2.checkbox("Bo loc S/R (Fractal)", value=current.get("sr_filter_enabled", True))
                max_spread = f2.number_input("Spread toi da (pips)", value=float(current.get("max_spread", 30.0)))
                news_filter = f2.checkbox("Bo loc tin tuc", value=current.get("news_filter_enabled", False))
                mt1, mt2 = st.columns(2)
                max_trades_time = mt1.number_input("So lenh toi da cung luc", value=int(current.get("max_trades_at_time", 3)),
                                                    min_value=1, max_value=50)
                max_trades_daily = mt2.number_input("So lenh toi da moi ngay", value=int(current.get("max_trades_daily", 10)),
                                                     min_value=1, max_value=200)

            # ── ATR ──────────────────────────────────────────────────── #
            with st.expander("📏 Cai dat ATR"):
                at1, at2 = st.columns(2)
                atr_period = at1.number_input("Chu ky ATR", value=int(current.get("atr_period", 14)), min_value=1)
                atr_tf = at2.selectbox("Khung thoi gian ATR", ["M1", "M5", "M15", "M30", "H1", "H4"],
                                        index=["M1", "M5", "M15", "M30", "H1", "H4"].index(current.get("atr_timeframe", "M5")))

            # ── Trade Management ─────────────────────────────────────── #
            with st.expander("🔄 Quan ly lenh"):
                st.markdown("**Dong mot phan**")
                pc = current.get("partial_close", {})
                pc1, pc2, pc3, pc4 = st.columns(4)
                pc_enabled = pc1.checkbox("Bat dong mot phan", value=pc.get("enabled", False))
                pc_trigger = pc2.number_input("Nguong % TP", value=float(pc.get("trigger_pct", 50.0)), min_value=1.0, max_value=99.0)
                pc_close = pc3.number_input("Dong % khoi luong", value=float(pc.get("close_pct", 50.0)), min_value=1.0, max_value=100.0)
                pc_be = pc4.checkbox("Dua SL ve hoa von", value=pc.get("move_sl_to_be", True))

                st.markdown("**Doi cat lo**")
                tr = current.get("trailing", {})
                tr1, tr2, tr3, tr4 = st.columns(4)
                tr_enabled = tr1.checkbox("Bat trailing", value=tr.get("enabled", False))
                tr_mode = tr2.selectbox("Che do trailing", ["PCT_TP", "HILO"],
                                         index=["PCT_TP", "HILO"].index(tr.get("mode", "PCT_TP")))
                tr_trigger = tr3.number_input("Nguong trailing %", value=float(tr.get("trigger_pct", 50.0)))
                tr_pct = tr4.number_input("Khoang trailing %", value=float(tr.get("trail_pct", 30.0)))

                st.markdown("**He thong Grid**")
                gr = current.get("grid", {})
                gr1, gr2, gr3 = st.columns(3)
                gr_enabled = gr1.checkbox("Bat Grid", value=gr.get("enabled", False))
                gr_levels = gr1.number_input("So tang Grid", value=int(gr.get("levels", 3)), min_value=1, max_value=10)
                gr_dist = gr2.number_input("Khoang cach (pips)", value=float(gr.get("distance_pips", 200.0)))
                gr_dist_mult = gr2.number_input("He so khoang cach", value=float(gr.get("distance_multiplier", 1.5)))
                gr_vol_mult = gr3.number_input("He so khoi luong", value=float(gr.get("volume_multiplier", 1.5)))
                gr_max_lot = gr3.number_input("Lot Grid toi da", value=float(gr.get("max_grid_lot", 1.0)))

            # ── Wave Detector ────────────────────────────────────────── #
            with st.expander("🌊 Tham so bo phat hien song"):
                wd1, wd2 = st.columns(2)
                htf_ef = wd1.number_input("EMA nhanh HTF", value=int(current.get("htf_ema_fast", 21)))
                htf_es = wd1.number_input("EMA cham HTF", value=int(current.get("htf_ema_slow", 50)))
                ltf_ef = wd2.number_input("EMA nhanh LTF", value=int(current.get("ltf_ema_fast", 8)))
                ltf_es = wd2.number_input("EMA cham LTF", value=int(current.get("ltf_ema_slow", 21)))
                sw1, sw2 = st.columns(2)
                sw_mult = sw1.number_input("He so ATR khi di ngang", value=float(current.get("sideways_atr_mult", 1.5)))
                sw_candles = sw2.number_input("So nen di ngang", value=int(current.get("sideways_candles", 10)))

                st.markdown("**Bo loc huong song**")
                wdf_options = ["BOTH", "BUY_ONLY", "SELL_ONLY"]
                wdf_labels  = ["🔄 Ca hai huong (len + xuong)", "📈 Chi MUA (xu huong tang)", "📉 Chi BAN (xu huong giam)"]
                wdf_current = current.get("wave_direction_filter", "BOTH")
                wdf_idx     = wdf_options.index(wdf_current) if wdf_current in wdf_options else 0
                wave_dir_filter = st.radio(
                    "Cho phep tin hieu cho:",
                    options=wdf_options,
                    format_func=lambda x: wdf_labels[wdf_options.index(x)],
                    index=wdf_idx,
                    horizontal=True,
                )

            # ── Advanced Risk ────────────────────────────────────────── #
            with st.expander("🛡️ Quan ly rui ro nang cao"):
                ar1, ar2, ar3 = st.columns(3)
                max_eq = ar1.number_input("Von tai khoan toi da ($, 0=tat)", value=float(current.get("max_account_equity", 0.0)), min_value=0.0)
                max_daily_dd = ar2.number_input("DD ngay toi da (%)", value=float(current.get("max_daily_dd_pct", 5.0)), min_value=0.1, max_value=100.0)
                max_overall_dd = ar3.number_input("DD tong toi da (%)", value=float(current.get("max_overall_dd_pct", 20.0)), min_value=0.1, max_value=100.0)

                cq1, cq2, cq3 = st.columns(3)
                max_q = cq1.number_input("Kich thuoc hang doi toi da", value=int(current.get("max_queue_size", 10)))
                cooldown = cq2.number_input("Hoi chieu (phut)", value=float(current.get("cooldown_minutes", 5.0)))
                expiry = cq3.number_input("Han tin hieu (giay)", value=float(current.get("signal_expiry_seconds", 300.0)))

            # ── Daily Lock Targets ───────────────────────────────────── #
            with st.expander("🎯 Muc tieu loi nhuan va thua lo ngay", expanded=True):
                st.markdown(
                    "Dat muc tieu theo ngay. Khi cham nguong, robot **tu dung** cho den khi ban dat lai thu cong. "
                    "Dat **0** de tat."
                )
                dl1, dl2 = st.columns(2)
                daily_profit_target = dl1.number_input(
                    "📈 Muc tieu loi nhuan ngay ($, 0=tat)",
                    value=float(current.get("daily_profit_target", 0.0)),
                    min_value=0.0,
                    step=10.0,
                    format="%.2f",
                    help="Robot tu dung khi Lai/Lo ngay ≥ gia tri nay",
                )
                daily_loss_limit = dl2.number_input(
                    "📉 Gioi han lo ngay ($, 0=tat)",
                    value=float(current.get("daily_loss_limit", 0.0)),
                    min_value=0.0,
                    step=10.0,
                    format="%.2f",
                    help="Robot tu dung khi Lai/Lo ngay ≤ -(gia tri nay)",
                )
                # Show suggested targets from backend
                suggested = api_get("/api/capital/suggest_targets", {})
                if suggested:
                    st.caption(
                        f"💡 Muc tieu goi y theo so du hien tai: "
                        f"Loi nhuan=${suggested.get('daily_profit_target', 0):.2f}, "
                        f"Thua lo=${suggested.get('daily_loss_limit', 0):.2f}"
                    )

            # ── Capital Profile ──────────────────────────────────────── #
            with st.expander("💼 Ho so von (tu dong canh chinh rui ro)"):
                st.markdown(
                    "Tu dong canh chinh lot va tham so rui ro theo quy mo tai khoan. "
                    "**AUTO** se tu nhan dien muc phu hop tu so du cua ban."
                )
                cp_options = [
                    "AUTO",
                    "NANO_500", "NANO_600", "NANO_700", "NANO_800", "NANO_900",
                    "MICRO", "SMALL", "MEDIUM", "LARGE", "CUSTOM",
                ]
                cp_labels  = [
                    "🤖 AUTO (tu nhan dien tu so du)",
                    "🔬 NANO ~$500 (< $600) — 0.01 lot, rui ro 0.5%",
                    "🔬 NANO ~$600 ($600–$699) — 0.02 lot, rui ro 0.7%",
                    "🔬 NANO ~$700 ($700–$799) — 0.03 lot, rui ro 0.8%",
                    "🔬 NANO ~$800 ($800–$899) — 0.05 lot, rui ro 1.0%",
                    "🔬 NANO ~$900 ($900–$999) — 0.07 lot, rui ro 1.0%",
                    "🔬 MICRO (< $1,000 tong quat)",
                    "🔹 SMALL ($1,000–$5,000)",
                    "🔷 MEDIUM ($5,000–$25,000)",
                    "💎 LARGE (≥ $25,000)",
                    "🛠️ CUSTOM (tu chinh thu cong)",
                ]
                cp_current = current.get("capital_profile", "AUTO")
                cp_idx     = cp_options.index(cp_current) if cp_current in cp_options else 0
                capital_profile = st.selectbox(
                    "Ho so von",
                    options=cp_options,
                    format_func=lambda x: cp_labels[cp_options.index(x)],
                    index=cp_idx,
                )
                # Show current profile info
                profile_info = api_get("/api/capital/profile", {})
                if profile_info:
                    pi1, pi2, pi3, pi4 = st.columns(4)
                    pi1.metric("Ho so", profile_info.get("profile", ""))
                    pi2.metric("Che do lot", profile_info.get("lot_mode", ""))
                    pi3.metric("Lot toi da", f"{profile_info.get('max_lot', 0):.2f}")
                    pi4.metric("DD ngay toi da", f"{profile_info.get('max_daily_dd', 0):.1f}%")
                    st.caption(profile_info.get("description", ""))

            submitted = st.form_submit_button("💾 Luu cai dat", use_container_width=True, type="primary")

        if submitted:
            new_settings = {
                "username": username,
                "magic_number": int(magic),
                "symbol": symbol,
                "timeframe": timeframe,
                "htf_timeframe": htf_tf,
                "lot_mode": lot_mode,
                "lot_value": float(lot_value),
                "min_lot": float(min_lot),
                "max_lot": float(max_lot),
                "pip_value_per_lot": float(pip_value),
                "martingale": {
                    "enabled": mg_enabled,
                    "multiplier": float(mg_mult),
                    "max_steps": int(mg_steps),
                },
                "sl_mode": sl_mode,
                "sl_value": float(sl_value),
                "tp_mode": tp_mode,
                "tp_value": float(tp_value),
                "entry_mode": entry_mode,
                "retrace_atr_mult": float(retrace_mult),
                "min_body_atr": float(min_body_atr),
                "retest_level_x": float(retest_lvl),
                "session": session,
                "dst_mode": dst_mode,
                "gmt_offset": float(gmt_offset),
                "monitoring_minutes": int(monitoring_minutes),
                "ema_filter_enabled": ema_filter,
                "ema_fast": int(ema_fast),
                "ema_slow": int(ema_slow),
                "sr_filter_enabled": sr_filter,
                "max_spread": float(max_spread),
                "news_filter_enabled": news_filter,
                "max_trades_at_time": int(max_trades_time),
                "max_trades_daily": int(max_trades_daily),
                "atr_period": int(atr_period),
                "atr_timeframe": atr_tf,
                "partial_close": {
                    "enabled": pc_enabled,
                    "trigger_pct": float(pc_trigger),
                    "close_pct": float(pc_close),
                    "move_sl_to_be": pc_be,
                },
                "trailing": {
                    "enabled": tr_enabled,
                    "mode": tr_mode,
                    "trigger_pct": float(tr_trigger),
                    "trail_pct": float(tr_pct),
                },
                "grid": {
                    "enabled": gr_enabled,
                    "levels": int(gr_levels),
                    "distance_pips": float(gr_dist),
                    "distance_multiplier": float(gr_dist_mult),
                    "volume_multiplier": float(gr_vol_mult),
                    "max_grid_lot": float(gr_max_lot),
                },
                "htf_ema_fast": int(htf_ef),
                "htf_ema_slow": int(htf_es),
                "ltf_ema_fast": int(ltf_ef),
                "ltf_ema_slow": int(ltf_es),
                "sideways_atr_mult": float(sw_mult),
                "sideways_candles": int(sw_candles),
                "wave_direction_filter": wave_dir_filter,
                "max_account_equity": float(max_eq),
                "max_daily_dd_pct": float(max_daily_dd),
                "max_overall_dd_pct": float(max_overall_dd),
                "max_queue_size": int(max_q),
                "cooldown_minutes": float(cooldown),
                "signal_expiry_seconds": float(expiry),
                "daily_profit_target": float(daily_profit_target),
                "daily_loss_limit": float(daily_loss_limit),
                "capital_profile": capital_profile,
            }
            result = api_post("/api/settings", new_settings)
            if result:
                st.success("✅ Da luu va ap dung cai dat!")
            else:
                st.error("Luu cai dat that bai.")


# ══════════════════════════════════════════════════════════════════════════ #
#  PAGE 5 — TRADE HISTORY                                                   #
# ══════════════════════════════════════════════════════════════════════════ #

with tab5:
    st.markdown("## 📜 Lich su giao dich")

    trades_data = api_get("/api/trades?page_size=200", {})
    trades = trades_data.get("trades", []) if trades_data else []
    closed = [t for t in trades if t.get("status") == "CLOSED"]
    open_t = [t for t in trades if t.get("status") == "OPEN"]

    if not trades:
        st.info("Chua co lich su giao dich. Hay bat dau robot de giao dich.")
    else:
        # Summary metrics
        h1, h2, h3, h4, h5 = st.columns(5)
        total_pnl_hist = sum(t.get("pnl", 0) for t in closed)
        wins = [t for t in closed if t.get("pnl", 0) > 0]
        losses = [t for t in closed if t.get("pnl", 0) <= 0]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
        win_rate_hist = len(wins) / len(closed) * 100 if closed else 0.0
        avg_win = gross_win / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0

        h1.metric("Tong Lai/Lo", f"${total_pnl_hist:,.2f}")
        h2.metric("Ty le thang", f"{win_rate_hist:.1f}%")
        h3.metric("He so loi nhuan", f"{pf:.2f}")
        h4.metric("Loi nhuan TB", f"${avg_win:.2f}")
        h5.metric("Thua lo TB", f"-${avg_loss:.2f}")

        st.markdown("---")

        col_chart, col_daily = st.columns([3, 2])

        with col_chart:
            # Equity curve
            if closed:
                df_hist = pd.DataFrame(closed)
                df_hist["open_time"] = pd.to_datetime(df_hist["open_time"], unit="s")
                df_hist = df_hist.sort_values("open_time")
                df_hist["cum_pnl"] = df_hist["pnl"].cumsum()

                fig_eq = go.Figure()
                bar_colors = ["#00e676" if p > 0 else "#ff5252" for p in df_hist["pnl"]]
                fig_eq.add_trace(go.Bar(
                    x=df_hist["open_time"],
                    y=df_hist["pnl"],
                    name="Lai/Lo moi lenh",
                    marker_color=bar_colors,
                ))
                fig_eq.add_trace(go.Scatter(
                    x=df_hist["open_time"],
                    y=df_hist["cum_pnl"],
                    name="Lai/Lo luy ke",
                    line=dict(color="#40c4ff", width=2),
                    yaxis="y2",
                ))
                fig_eq.update_layout(
                    title="Lai/Lo tung lenh + duong cong von",
                    height=350,
                    paper_bgcolor="#0e1117",
                    plot_bgcolor="#0e1117",
                    font=dict(color="#fafafa"),
                    xaxis=dict(gridcolor="#1e2130"),
                    yaxis=dict(gridcolor="#1e2130", title="Lai/Lo moi lenh"),
                    yaxis2=dict(overlaying="y", side="right", gridcolor="#1e2130", title="Luy ke"),
                    legend=dict(bgcolor="rgba(30,33,48,0.8)"),
                )
                st.plotly_chart(fig_eq, use_container_width=True)

                # Drawdown chart
                peak = df_hist["cum_pnl"].cummax()
                drawdown = df_hist["cum_pnl"] - peak
                fig_dd = go.Figure()
                fig_dd.add_trace(go.Scatter(
                    x=df_hist["open_time"],
                    y=drawdown,
                    fill="tozeroy",
                    name="Drawdown",
                    line=dict(color="#ff5252"),
                    fillcolor="rgba(255,82,82,0.2)",
                ))
                fig_dd.update_layout(
                    title="Bieu do drawdown",
                    height=200,
                    paper_bgcolor="#0e1117",
                    plot_bgcolor="#0e1117",
                    font=dict(color="#fafafa"),
                    xaxis=dict(gridcolor="#1e2130"),
                    yaxis=dict(gridcolor="#1e2130", title="Drawdown ($)"),
                )
                st.plotly_chart(fig_dd, use_container_width=True)

        with col_daily:
            # Daily P&L breakdown
            if closed:
                df_daily = pd.DataFrame(closed)
                df_daily["date"] = pd.to_datetime(df_daily["open_time"], unit="s").dt.date
                daily_pnl = df_daily.groupby("date")["pnl"].sum().reset_index()
                daily_pnl.columns = ["Date", "P&L"]
                daily_pnl["Color"] = daily_pnl["P&L"].apply(lambda x: "🟢" if x > 0 else "🔴")

                st.markdown("### Lai/Lo theo ngay")
                st.dataframe(
                    daily_pnl.assign(**{"P&L": daily_pnl["P&L"].map(lambda x: f"${x:+.2f}")}),
                    use_container_width=True,
                    hide_index=True,
                )

                # Direction breakdown
                dir_counts = df_hist["direction"].value_counts().reset_index()
                dir_counts.columns = ["Direction", "Count"]
                fig_pie = go.Figure(data=[go.Pie(
                    labels=dir_counts["Direction"],
                    values=dir_counts["Count"],
                    marker_colors=["#00e676", "#ff5252"],
                    hole=0.4,
                )])
                fig_pie.update_layout(
                    title="MUA so voi BAN",
                    height=220,
                    paper_bgcolor="#0e1117",
                    font=dict(color="#fafafa"),
                )
                st.plotly_chart(fig_pie, use_container_width=True)

        # Full trade table
        st.markdown("### Tat ca lenh da dong")
        if closed:
            df_table = pd.DataFrame(closed)
            display_cols = ["trade_id", "symbol", "direction", "lot_size",
                            "entry_price", "close_price", "sl", "tp", "pnl",
                            "entry_mode", "status"]
            available = [c for c in display_cols if c in df_table.columns]
            if "open_time" in df_table.columns:
                df_table["open_time"] = pd.to_datetime(df_table["open_time"], unit="s").dt.strftime("%Y-%m-%d %H:%M")

            def pnl_color(val):
                if isinstance(val, (int, float)):
                    return "color: #00e676" if val > 0 else "color: #ff5252"
                return ""

            styled_table = df_table[available].style.applymap(pnl_color, subset=["pnl"] if "pnl" in available else [])
            st.dataframe(styled_table, use_container_width=True, hide_index=True)

        if open_t:
            st.markdown("### Lenh mo")
            df_open_t = pd.DataFrame(open_t)
            st.dataframe(df_open_t, use_container_width=True, hide_index=True)

    # Total stats footer
    st.markdown("---")
    st.markdown(f"*Tong ban ghi: {trades_data.get('total', 0) if trades_data else 0} | "
                f"Dang hien thi: {len(trades)} | Cap nhat luc: {datetime.now().strftime('%H:%M:%S')}*")


# ══════════════════════════════════════════════════════════════════════════ #
#  PAGE 6 — AI & CONTROLS                                                    #
# ══════════════════════════════════════════════════════════════════════════ #

with tab6:
    st.markdown("## 🤖 AI va dieu khien")

    col_ai, col_ctrl = st.columns([3, 2])

    with col_ai:
        # ── LLM Orchestrator ─────────────────────────────────────── #
        st.markdown("### 🧠 Dieu phoi LLM")
        llm_status = api_get("/api/llm/status", {})
        if llm_status:
            enabled = llm_status.get("enabled", False)
            backend = llm_status.get("model", "Chua cau hinh")
            rag_size = llm_status.get("vector_store_size", 0)
            last_action = llm_status.get("last_action", "IDLE")

            la1, la2, la3 = st.columns(3)
            la1.metric(
                "Trang thai LLM",
                "✅ Dang hoat dong" if enabled else "⚠️ Che do gia lap",
            )
            la2.metric("Mo hinh", backend if enabled else "—")
            la3.metric("Bo nho RAG", f"{rag_size} tai lieu")
            if not enabled:
                st.info(
                    "💡 De bat LLM, hay dat bien moi truong **OPENAI_API_KEY** hoac **GEMINI_API_KEY**. "
                    "Trong luc do robot se dung quy tac de ra quyet dinh."
                )
            st.caption(f"Hanh dong gan nhat: {last_action}")
        else:
            st.warning("Khong the tai trang thai LLM.")

        st.markdown("---")

        # ── Ask LLM ──────────────────────────────────────────────── #
        st.markdown("### 💬 Hoi tro ly AI")
        user_q = st.text_area(
            "Cau hoi cua ban ve thi truong / trang thai robot:",
            placeholder=(
                "vi du: Thi truong hien tai dang o trang thai nao va co nen giao dich ngay khong? "
                "Hoac: Vi sao lenh gan nhat bi tu choi?"
            ),
            height=100,
        )
        if st.button("🤖 Hoi AI", type="primary", disabled=not user_q.strip()):
            with st.spinner("Dang suy nghi..."):
                resp = api_post("/api/llm/ask", {"prompt": user_q})
            if resp:
                st.markdown("**Tra loi cua AI:**")
                st.markdown(f"> {resp.get('answer', 'Khong co cau tra loi')}")
                st.caption(f"Backend: {resp.get('backend', 'KHONG_CO')}")
            else:
                st.error("LLM khong phan hoi. Kiem tra OPENAI_API_KEY / GEMINI_API_KEY.")

        # ── LLM call log ─────────────────────────────────────────── #
        if llm_status:
            call_log = llm_status.get("function_call_log", [])
            if call_log:
                st.markdown("### 📋 Quyet dinh AI gan day")
                for entry in reversed(call_log[-5:]):
                    ts = datetime.fromtimestamp(entry.get("ts", 0)).strftime("%H:%M:%S")
                    st.markdown(
                        f"**{ts}** [{entry.get('backend','?')}] "
                        f"`{entry.get('prompt', '')[:80]}` → "
                        f"_{entry.get('answer', '')[:120]}_"
                    )

    with col_ctrl:
        # ── Daily Lock Controls ───────────────────────────────────── #
        st.markdown("### 🔒 Dieu khien khoa ngay")
        lock_info = api_get("/api/risk/daily_lock", {})
        if lock_info:
            locked   = lock_info.get("locked", False)
            p_lock   = lock_info.get("profit_locked", False)
            l_lock   = lock_info.get("loss_locked", False)
            dpnl     = lock_info.get("daily_pnl", 0.0)
            dtarget  = lock_info.get("daily_profit_target", 0.0)
            dlimit   = lock_info.get("daily_loss_limit", 0.0)

            if p_lock:
                st.success(f"🏆 Da dat muc tieu loi nhuan! Lai/Lo ngay: ${dpnl:+.2f}")
            elif l_lock:
                st.error(f"🛑 Da cham gioi han lo! Lai/Lo ngay: ${dpnl:+.2f}")
            else:
                st.info(f"Lai/Lo ngay: ${dpnl:+.2f}")
                if dtarget > 0:
                    prog = min(max(dpnl / dtarget, 0), 1)
                    st.progress(prog, text=f"Muc tieu loi nhuan: {prog:.0%} (${dtarget:.2f})")
                if dlimit > 0:
                    loss_prog = min(max(-dpnl / dlimit, 0), 1)
                    if loss_prog > 0:
                        st.progress(loss_prog, text=f"Muc lo da dung: {loss_prog:.0%} (${dlimit:.2f})")

            if locked:
                lock_reason = lock_info.get("lock_reason", "")
                if lock_reason:
                    st.markdown(f"**Ly do khoa:** _{lock_reason}_")
                if st.button("🔓 Dat lai khoa ngay va tiep tuc", type="primary", use_container_width=True):
                    result = api_post("/api/robot/reset_daily_lock")
                    if result:
                        st.success("✅ Da dat lai khoa ngay. Ban co the khoi dong lai robot.")
                        st.rerun()

        st.markdown("---")

        # ── Candle Library Status ─────────────────────────────────── #
        st.markdown("### 📚 Thu vien nen")
        cl_status = api_get("/api/candle_library/status", {})
        if cl_status:
            total   = cl_status.get("total_candles", 0)
            cap     = cl_status.get("capacity", 10000)
            last_ts = cl_status.get("last_updated", 0)
            rt      = cl_status.get("realtime_enabled", False)

            clc1, clc2 = st.columns(2)
            clc1.metric("So nen da luu", f"{total:,}")
            clc2.metric("Suc chua", f"{cap:,}")
            pct = total / cap if cap > 0 else 0
            st.progress(pct, text=f"Muc day thu vien: {pct:.0%}")
            if last_ts > 0:
                st.caption(
                    f"Cap nhat cuoi: {datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"Thoi gian thuc: {'✅' if rt else '❌'}"
                )
            if total < 100:
                st.info("📡 Dang xay dung thu vien nen... robot can chay de thu thap du lieu.")
        else:
            st.warning("Khong co trang thai thu vien nen.")

        st.markdown("---")

        # ── Capital Profile ───────────────────────────────────────── #
        st.markdown("### 💼 Ho so von")
        profile_info = api_get("/api/capital/profile", {})
        if profile_info:
            st.metric("Ho so dang ap dung", profile_info.get("profile", "?"))
            pi1, pi2 = st.columns(2)
            pi1.metric("Che do lot", profile_info.get("lot_mode", ""))
            pi2.metric("Gia tri lot", f"{profile_info.get('lot_value', 0):.3f}")
            pi3, pi4 = st.columns(2)
            pi3.metric("Lot toi da", f"{profile_info.get('max_lot', 0):.2f}")
            pi4.metric("DD ngay toi da", f"{profile_info.get('max_daily_dd', 0):.1f}%")
            st.caption(profile_info.get("description", ""))
        else:
            st.warning("Khong co thong tin ho so von.")

        st.markdown("---")

        # ── Wave Direction Filter ─────────────────────────────────── #
        st.markdown("### 🌊 Bo loc song (doi nhanh)")
        current_settings = api_get("/api/settings", {})
        wf = current_settings.get("wave_direction_filter", "BOTH")
        wf_emoji = {"BOTH": "🔄", "BUY_ONLY": "📈", "SELL_ONLY": "📉"}.get(wf, "?")
        wf_label = {
            "BOTH": "Ca hai huong",
            "BUY_ONLY": "Chi MUA",
            "SELL_ONLY": "Chi BAN",
        }.get(wf, wf)
        st.metric("Bo loc hien tai", f"{wf_emoji} {wf_label}")
        wf_options = ["BOTH", "BUY_ONLY", "SELL_ONLY"]
        new_wf = st.radio("Doi sang:", wf_options,
                          index=wf_options.index(wf) if wf in wf_options else 0,
                          horizontal=True,
                          format_func=lambda x: {
                              "BOTH": "Ca hai huong",
                              "BUY_ONLY": "Chi MUA",
                              "SELL_ONLY": "Chi BAN",
                          }.get(x, x),
                          label_visibility="collapsed")
        if new_wf != wf:
            if st.button("Ap dung bo loc moi", use_container_width=True):
                # Note: This sends the full current settings with the wave filter
                # updated. For production use, a dedicated PATCH endpoint would be
                # preferable. For now, the full settings round-trip is safe since
                # current_settings was just fetched from the backend.
                updated = dict(current_settings)
                updated["wave_direction_filter"] = new_wf
                result = api_post("/api/settings", updated)
                if result:
                    st.success("✅ Da cap nhat bo loc song.")
                    st.rerun()
