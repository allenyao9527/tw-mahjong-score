# mahjong_score.py
import json
from datetime import datetime
from dataclasses import dataclass, field, asdict, is_dataclass
from typing import List, Dict, Any, Optional

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

APP_VERSION = "v2026-02-11_04_ios_ls_fix_streamlit_cloud"
WINDS = ["東", "南", "西", "北"]

LOCAL_STORAGE_KEY = "tw_mj_score_state_v2_htmlbridge"


# ============================
# 1) Models
# ============================
@dataclass
class Settings:
    base: int = 300
    tai_value: int = 100

    players: List[str] = field(default_factory=lambda: ["玩家1", "玩家2", "玩家3", "玩家4"])
    seat_players: List[int] = field(default_factory=lambda: [0, 1, 2, 3])

    draw_keeps_dealer: bool = True

    host_player_id: int = 0
    dong_per_self_draw: int = 0
    dong_cap_total: int = 0


# ============================
# 2) LocalStorage Bridge (safe)
# ============================
def _ls_read(key: str, nonce: int) -> Optional[str]:
    """
    Read localStorage[key] from client.
    On Streamlit Cloud/iOS some timings can fail; must be best-effort.
    """
    html = f"""
    <script>
      (function() {{
        try {{
          const k = {json.dumps(key)};
          const v = window.localStorage.getItem(k) || "";
          if (window.Streamlit) {{
            window.Streamlit.setComponentValue(v);
            window.Streamlit.setFrameHeight(0);
          }}
        }} catch (e) {{
          if (window.Streamlit) {{
            window.Streamlit.setComponentValue("");
            window.Streamlit.setFrameHeight(0);
          }}
        }}
      }})();
    </script>
    """
    try:
        val = components.html(html, height=0, key=f"ls_read_{key}_{nonce}")
        if val is None:
            return None
        return str(val)
    except Exception as e:
        st.session_state["ls_last_error"] = f"ls_read_error: {type(e).__name__}"
        return None


def _ls_write(key: str, value: str, nonce: int) -> Optional[str]:
    html = f"""
    <script>
      (function() {{
        try {{
          const k = {json.dumps(key)};
          const v = {json.dumps(value)};
          window.localStorage.setItem(k, v);
          if (window.Streamlit) {{
            window.Streamlit.setComponentValue("ok");
            window.Streamlit.setFrameHeight(0);
          }}
        }} catch (e) {{
          if (window.Streamlit) {{
            window.Streamlit.setComponentValue("err");
            window.Streamlit.setFrameHeight(0);
          }}
        }}
      }})();
    </script>
    """
    try:
        ack = components.html(html, height=0, key=f"ls_write_{key}_{nonce}")
        if ack is None:
            return None
        return str(ack)
    except Exception as e:
        st.session_state["ls_last_error"] = f"ls_write_error: {type(e).__name__}"
        return None


def _ls_remove(key: str, nonce: int) -> Optional[str]:
    html = f"""
    <script>
      (function() {{
        try {{
          const k = {json.dumps(key)};
          window.localStorage.removeItem(k);
          if (window.Streamlit) {{
            window.Streamlit.setComponentValue("ok");
            window.Streamlit.setFrameHeight(0);
          }}
        }} catch (e) {{
          if (window.Streamlit) {{
            window.Streamlit.setComponentValue("err");
            window.Streamlit.setFrameHeight(0);
          }}
        }}
      }})();
    </script>
    """
    try:
        ack = components.html(html, height=0, key=f"ls_rm_{key}_{nonce}")
        if ack is None:
            return None
        return str(ack)
    except Exception as e:
        st.session_state["ls_last_error"] = f"ls_remove_error: {type(e).__name__}"
        return None


def snapshot_state() -> Dict[str, Any]:
    s = st.session_state.settings
    settings_dict = asdict(s) if is_dataclass(s) else dict(s)
    return {
        "settings": settings_dict,
        "events": st.session_state.get("events", []),
        "sessions": st.session_state.get("sessions", []),
    }


def restore_state(data: Dict[str, Any]) -> None:
    if not data:
        return
    if isinstance(data.get("settings"), dict):
        try:
            st.session_state.settings = Settings(**data["settings"])
        except Exception:
            st.session_state.settings = Settings()
    st.session_state.events = data.get("events", []) or []
    st.session_state.sessions = data.get("sessions", []) or []


def autosave() -> None:
    """
    Best-effort save to localStorage.
    """
    try:
        payload = json.dumps(snapshot_state(), ensure_ascii=False)
        st.session_state["last_saved_len"] = len(payload)

        st.session_state["ls_nonce"] = st.session_state.get("ls_nonce", 0) + 1
        ack = _ls_write(LOCAL_STORAGE_KEY, payload, st.session_state["ls_nonce"])
        st.session_state["last_save_ack"] = ack

        # Optional probe (best-effort)
        st.session_state["ls_nonce"] = st.session_state.get("ls_nonce", 0) + 1
        probe = _ls_read(LOCAL_STORAGE_KEY, st.session_state["ls_nonce"])
        st.session_state["ls_probe_len"] = len(probe or "")
    except Exception:
        pass


def try_restore_from_local_storage():
    """
    ✅ IMPORTANT:
    Do NOT run inside init_state() on Streamlit Cloud.
    Run after page render starts (safe timing), and only once.
    """
    if st.session_state.get("cloud_loaded", False):
        return

    st.session_state.setdefault("ls_read_tries", 0)
    for _ in range(3):
        st.session_state["ls_nonce"] = st.session_state.get("ls_nonce", 0) + 1
        st.session_state["ls_read_tries"] += 1
        raw = _ls_read(LOCAL_STORAGE_KEY, st.session_state["ls_nonce"])
        if raw:
            st.session_state["ls_loaded_len"] = len(raw)
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    restore_state(data)
            except Exception:
                pass
            break

    st.session_state["cloud_loaded"] = True


# ============================
# 3) State / Helpers
# ============================
def init_state():
    st.session_state.setdefault("settings", Settings())
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("sessions", [])

    st.session_state.setdefault("selected_seat", None)
    st.session_state.setdefault("debug", True)

    st.session_state.setdefault("hand_res", "自摸")
    st.session_state.setdefault("hand_tai", 0)
    st.session_state.setdefault("hand_win", 0)
    st.session_state.setdefault("hand_lose", 0)

    st.session_state.setdefault("pen_pt", "詐胡")
    st.session_state.setdefault("pen_off", 0)
    st.session_state.setdefault("pen_vic", 0)
    st.session_state.setdefault("pen_amt", 300)

    st.session_state.setdefault("reset_hand_inputs", False)
    st.session_state.setdefault("reset_pen_inputs", False)

    # ✅ 延遲載入 localStorage：不要在 init_state 直接讀（Streamlit Cloud 會炸）
    st.session_state.setdefault("cloud_loaded", False)
    st.session_state.setdefault("ls_nonce", 0)
    st.session_state.setdefault("ls_read_tries", 0)

    # Debug probes
    st.session_state.setdefault("last_saved_len", 0)
    st.session_state.setdefault("last_save_ack", None)
    st.session_state.setdefault("ls_probe_len", 0)
    st.session_state.setdefault("ls_loaded_len", 0)
    st.session_state.setdefault("ls_last_error", "")


def safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def amount_A(settings: Settings, tai: int) -> int:
    return safe_int(settings.base) + safe_int(tai) * safe_int(settings.tai_value)


def dealer_bonus_tai(dealer_run: int) -> int:
    return 1 + 2 * int(dealer_run)


def ev_to_dict(ev: Any) -> Dict[str, Any]:
    if isinstance(ev, dict):
        d = dict(ev)
    elif is_dataclass(ev):
        d = asdict(ev)
    else:
        d = {}
        for k in (
            "result", "winner_id", "loser_id", "tai",
            "p_type", "offender_id", "victim_id", "amount",
        ):
            if hasattr(ev, k):
                d[k] = getattr(ev, k)

    if "result" in d:
        d["_type"] = "hand"
    elif "p_type" in d:
        d["_type"] = "penalty"
    else:
        d["_type"] = d.get("_type", "unknown")
    return d


def normalize_events(events: List[Any]) -> List[Dict[str, Any]]:
    return [ev_to_dict(e) for e in events]


# ============================
# 4) Core compute
# ============================
def compute_game_state(settings: Settings, events_raw: List[Any]):
    events = normalize_events(events_raw)

    n = 4
    names = settings.players
    seat_players = settings.seat_players

    cum = [0] * n
    rows = []

    rw, ds, dr, d_acc = 0, 0, 0, 0
    debug_steps = []

    stats = {pid: {"自摸": 0, "胡": 0, "放槍": 0, "詐胡": 0, "詐摸": 0} for pid in range(n)}

    def hand_label(rw_idx: int, dealer_seat: int) -> str:
        return f"{WINDS[rw_idx]}{dealer_seat + 1}局"

    def advance_dealer():
        nonlocal rw, ds, dr
        ds = (ds + 1) % 4
        dr = 0
        if ds == 0:
            rw = (rw + 1) % 4

    for idx, ev in enumerate(events, start=1):
        delta = [0] * n
        label = ""
        desc = ""

        dealer_pid = seat_players[ds]
        bonus = dealer_bonus_tai(dr)

        if ev.get("_type") == "hand":
            label = hand_label(rw, ds)

            result = ev.get("result", "")
            w = safe_int(ev.get("winner_id", 0))
            l = safe_int(ev.get("loser_id", 0))
            tai = safe_int(ev.get("tai", 0))
            A = amount_A(settings, tai)

            if result == "流局":
                desc = "流局"
                if settings.draw_keeps_dealer:
                    dr += 1
                else:
                    advance_dealer()

            elif result == "自摸":
                if 0 <= w < n:
                    stats[w]["自摸"] += 1

                if w == dealer_pid:
                    desc = f"{names[w]} 自摸({tai}台) [莊]"
                    for p in range(n):
                        if p == w:
                            delta[p] += 3 * A
                        else:
                            delta[p] -= A
                    dr += 1
                else:
                    dealer_pay = amount_A(settings, tai + bonus)
                    other_pay = A
                    desc = f"{names[w]} 自摸({tai}台) [閒] (莊付{tai}+{bonus}台)"
                    for p in range(n):
                        if p == w:
                            delta[p] += dealer_pay + 2 * other_pay
                        elif p == dealer_pid:
                            delta[p] -= dealer_pay
                        else:
                            delta[p] -= other_pay
                    advance_dealer()

                # 東錢（可選）—已算入總分
                if settings.dong_per_self_draw > 0 and settings.dong_cap_total > 0:
                    remain = max(0, int(settings.dong_cap_total) - int(d_acc))
                    take = min(int(settings.dong_per_self_draw), remain)
                    if take > 0:
                        delta[w] -= take
                        delta[int(settings.host_player_id)] += take
                        d_acc += take

            elif result == "放槍":
                if w == l:
                    desc = "錯誤：胡牌者=放槍者"
                else:
                    if 0 <= w < n:
                        stats[w]["胡"] += 1
                    if 0 <= l < n:
                        stats[l]["放槍"] += 1

                    if w == dealer_pid:
                        desc = f"{names[w]} 胡 {names[l]}({tai}台) [莊]"
                        delta[w] += A
                        delta[l] -= A
                        dr += 1
                    else:
                        if l == dealer_pid:
                            pay = amount_A(settings, tai + bonus)
                            desc = f"{names[w]} 胡 {names[l]}({tai}台) [閒胡莊] (莊付{tai}+{bonus}台)"
                            delta[w] += pay
                            delta[l] -= pay
                        else:
                            desc = f"{names[w]} 胡 {names[l]}({tai}台)"
                            delta[w] += A
                            delta[l] -= A
                        advance_dealer()
            else:
                desc = f"未知牌局結果：{result}"

        elif ev.get("_type") == "penalty":
            label = hand_label(rw, ds)
            p_type = ev.get("p_type", "")
            amt = safe_int(ev.get("amount", 0))

            dealer_paid = False

            if p_type == "詐胡":
                off = safe_int(ev.get("offender_id", 0))
                vic = safe_int(ev.get("victim_id", 0))
                if 0 <= off < n:
                    stats[off]["詐胡"] += 1
                desc = f"{names[off]} 詐胡→{names[vic]} (${amt})"
                delta[off] -= amt
                delta[vic] += amt
                dealer_paid = (off == dealer_pid)

            elif p_type == "詐摸":
                off = safe_int(ev.get("offender_id", 0))
                if 0 <= off < n:
                    stats[off]["詐摸"] += 1

                if off == dealer_pid:
                    desc = f"{names[off]} 詐摸賠三家 (每家${amt}) [莊]"
                    delta[off] -= 3 * amt
                    for p in range(n):
                        if p != off:
                            delta[p] += amt
                    dealer_paid = True
                else:
                    bonus_tai = dealer_bonus_tai(dr)
                    dealer_extra = bonus_tai * int(settings.tai_value)

                    other_non_dealers = [p for p in range(n) if p not in (off, dealer_pid)]
                    for p in other_non_dealers:
                        delta[off] -= amt
                        delta[p] += amt

                    pay_dealer = amt + dealer_extra
                    delta[off] -= pay_dealer
                    delta[dealer_pid] += pay_dealer

                    desc = (
                        f"{names[off]} 詐摸[閒]：賠兩閒各${amt}；"
                        f"賠莊${amt}+{bonus_tai}台(每台{int(settings.tai_value)})=${pay_dealer}"
                    )
                    dealer_paid = False
            else:
                desc = f"未知罰則類型：{p_type}"

            if dealer_paid:
                advance_dealer()
            else:
                dr += 1

        else:
            label = "未知"
            desc = f"不支援事件型別：{type(events_raw[idx-1])}"

        for p in range(n):
            cum[p] += delta[p]

        row = {"#": idx, "類型": label, "說明": desc}
        for p in range(n):
            row[names[p]] = cum[p]
        rows.append(row)

        debug_steps.append(
            f"[#{idx}] ds={ds} dealer={names[dealer_pid]} dr={dr} rw={rw} bonusTai={bonus} delta={delta} cum={cum}"
        )

    ledger_df = pd.DataFrame(rows)
    sum_df = pd.DataFrame([{"玩家": names[i], "總分": cum[i]} for i in range(n)])

    stats_rows = []
    for pid in range(n):
        r = {"玩家": names[pid]}
        r.update(stats[pid])
        stats_rows.append(r)
    stats_df = pd.DataFrame(stats_rows)

    return ledger_df, sum_df, stats_df, rw, ds, dr, d_acc, debug_steps


def _apply_reset_flags_before_widgets():
    if st.session_state.get("reset_hand_inputs"):
        st.session_state["hand_res"] = "自摸"
        st.session_state["hand_tai"] = 0
        st.session_state["hand_win"] = 0
        st.session_state["hand_lose"] = 0
        st.session_state["reset_hand_inputs"] = False

    if st.session_state.get("reset_pen_inputs"):
        st.session_state["pen_pt"] = "詐胡"
        st.session_state["pen_off"] = 0
        st.session_state["pen_vic"] = 0
        st.session_state["pen_amt"] = int(st.session_state.settings.base)
        st.session_state["reset_pen_inputs"] = False


# ============================
# 5) UI
# ============================
def page_settings(s: Settings):
    try_restore_from_local_storage()

    st.header("⚙️ 設定")
    st.caption(f"版本：{APP_VERSION}")

    with st.form("set_form"):
        cols = st.columns(4)
        new_players = [cols[i].text_input(f"玩家{i+1}", value=s.players[i], key=f"p_{i}") for i in range(4)]

        st.divider()
        c1, c2 = st.columns(2)
        base = c1.number_input("底", min_value=0, value=int(s.base), step=50)
        tai_value = c2.number_input("每台金額", min_value=0, value=int(s.tai_value), step=10)

        st.divider()
        draw_keep = st.toggle("流局連莊", value=bool(s.draw_keeps_dealer))

        st.divider()
        st.subheader("東（可選）")
        host = st.selectbox(
            "場主(東錢收款者)",
            options=[0, 1, 2, 3],
            index=int(s.host_player_id),
            format_func=lambda pid: new_players[pid],
        )
        c3, c4 = st.columns(2)
        dong_x = c3.number_input("自摸扣東（每次）", min_value=0, value=int(s.dong_per_self_draw), step=10)
        dong_cap = c4.number_input("東錢上限（累計）", min_value=0, value=int(s.dong_cap_total), step=50)

        save = st.form_submit_button("💾 儲存設定", use_container_width=True)

    if save:
        s.players = new_players
        s.base = int(base)
        s.tai_value = int(tai_value)
        s.draw_keeps_dealer = bool(draw_keep)
        s.host_player_id = int(host)
        s.dong_per_self_draw = int(dong_x)
        s.dong_cap_total = int(dong_cap)

        st.session_state.settings = s
        autosave()
        st.success("✅ 已儲存設定（本機已保存）")
        st.rerun()


def render_seat_map(s: Settings, sum_df: pd.DataFrame, dealer_seat: int):
    def seat_btn(seat_idx: int, container):
        pid = s.seat_players[seat_idx]
        name = s.players[pid]
        score = int(sum_df.loc[sum_df["玩家"] == name, "總分"].values[0]) if not sum_df.empty else 0
        is_dealer = (seat_idx == dealer_seat)
        mark = " 🀄" if is_dealer else ""
        prefix = "👉 " if st.session_state.selected_seat == seat_idx else ""
        label = f"{prefix}{WINDS[seat_idx]}：{name}{mark} (${score})"

        if container.button(label, key=f"seatbtn_{seat_idx}", use_container_width=True):
            if st.session_state.selected_seat is None:
                st.session_state.selected_seat = seat_idx
            else:
                o = st.session_state.selected_seat
                s.seat_players[o], s.seat_players[seat_idx] = s.seat_players[seat_idx], s.seat_players[o]
                st.session_state.selected_seat = None

            st.session_state.settings = s
            autosave()
            st.rerun()

    top = st.columns([1, 1.5, 1])
    seat_btn(1, top[1])  # 南
    mid = st.columns([1, 1.5, 1])
    seat_btn(2, mid[0])  # 西
    seat_btn(0, mid[2])  # 東
    bot = st.columns([1, 1.5, 1])
    seat_btn(3, bot[1])  # 北


def end_current_session(s: Settings):
    events = st.session_state.events
    ledger_df, sum_df, stats_df, rw, ds, dr, d_acc, _ = compute_game_state(s, events)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session = {
        "ended_at": stamp,
        "event_count": len(events),
        "dong_total": int(d_acc),
        "sum_df": sum_df.to_dict(orient="records"),
        "stats_df": stats_df.to_dict(orient="records"),
        "ledger_tail": ledger_df.tail(20).to_dict(orient="records"),
    }
    st.session_state.sessions.append(session)

    st.session_state.events = []
    st.session_state["reset_hand_inputs"] = True
    st.session_state["reset_pen_inputs"] = True
    autosave()


def page_record(s: Settings):
    try_restore_from_local_storage()

    st.header("🀄 牌局錄入")
    _apply_reset_flags_before_widgets()

    ledger_df, sum_df, stats_df, rw, ds, dr, d_acc, debug_steps = compute_game_state(s, st.session_state.events)

    st.subheader(f"目前局數：{WINDS[rw]}{ds+1}局 (連{dr})")
    st.caption("莊家依局數固定：東→南→西→北（只能調整玩家座位，不可手動改莊位）。")

    st.divider()
    render_seat_map(s, sum_df, dealer_seat=ds)

    st.divider()
    b1, b2, b3 = st.columns([1, 1, 1])

    if b1.button("🏁 結束牌局（封存並新開）", use_container_width=True):
        if len(st.session_state.events) == 0:
            st.warning("目前沒有事件，無需結束。")
        else:
            end_current_session(s)
            st.success("已封存本局並開始新局（本機已保存）。")
            st.rerun()

    if b2.button("🧹 清空本局（保留封存）", use_container_width=True):
        st.session_state.events = []
        st.session_state["reset_hand_inputs"] = True
        st.session_state["reset_pen_inputs"] = True
        autosave()
        st.rerun()

    if b3.button("🗑️ 清除本機暫存（全部重置）", use_container_width=True):
        st.session_state["ls_nonce"] = st.session_state.get("ls_nonce", 0) + 1
        _ls_remove(LOCAL_STORAGE_KEY, st.session_state["ls_nonce"])

        st.session_state.settings = Settings()
        st.session_state.events = []
        st.session_state.sessions = []
        st.session_state.selected_seat = None
        st.session_state["reset_hand_inputs"] = True
        st.session_state["reset_pen_inputs"] = True
        st.session_state["cloud_loaded"] = True
        st.rerun()

    mode = st.radio("輸入類型", ["一般", "罰則"], horizontal=True)

    if mode == "一般":
        res = st.selectbox("結果", ["自摸", "放槍", "流局"], key="hand_res")
        tai = st.number_input("台數", min_value=0, step=1, key="hand_tai")

        win = 0
        lose = 0

        if res in ("自摸", "放槍"):
            win = st.selectbox("贏家", [0, 1, 2, 3], format_func=lambda x: s.players[x], key="hand_win")
        if res == "放槍":
            lose = st.selectbox("放槍家", [0, 1, 2, 3], format_func=lambda x: s.players[x], key="hand_lose")

        submit = st.button("✅ 提交結果", use_container_width=True)
        if submit:
            if res == "放槍" and int(win) == int(lose):
                st.error("放槍時：贏家與放槍家不能相同")
            else:
                ev = {"_type": "hand", "result": res, "winner_id": int(win), "loser_id": int(lose), "tai": int(tai)}
                st.session_state.events.append(ev)
                st.session_state["reset_hand_inputs"] = True
                autosave()
                st.rerun()

    else:
        pt = st.selectbox("種類", ["詐胡", "詐摸"], key="pen_pt")
        off = st.selectbox("違規者", [0, 1, 2, 3], format_func=lambda x: s.players[x], key="pen_off")

        vic = 0
        if pt == "詐胡":
            vic = st.selectbox("賠付對象", [0, 1, 2, 3], format_func=lambda x: s.players[x], key="pen_vic")

        amt = st.number_input("金額", min_value=0, step=50, key="pen_amt")

        submit = st.button("🚨 提交罰則", use_container_width=True)
        if submit:
            ev = {"_type": "penalty", "p_type": pt, "offender_id": int(off), "victim_id": int(vic), "amount": int(amt)}
            st.session_state.events.append(ev)
            st.session_state["reset_pen_inputs"] = True
            autosave()
            st.rerun()

    c1, c2 = st.columns(2)
    if c1.button("🔙 撤銷上一筆", use_container_width=True):
        if st.session_state.events:
            st.session_state.events.pop()
            autosave()
            st.rerun()

    if c2.button("🧹 清空全部（本局+封存）", use_container_width=True):
        st.session_state.events = []
        st.session_state.sessions = []
        st.session_state["reset_hand_inputs"] = True
        st.session_state["reset_pen_inputs"] = True
        autosave()
        st.rerun()

    st.divider()
    st.info(f"💰 累計東錢：${int(d_acc)}（已算入總分）")

    if not ledger_df.empty:
        st.dataframe(ledger_df, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("DEBUG")
    st.session_state.debug = st.toggle("顯示 Debug", value=bool(st.session_state.debug))
    if st.session_state.debug:
        st.write(f"DEBUG events len: {len(st.session_state.events)}")
        st.write("DEBUG sessions len:", len(st.session_state.sessions))
        st.write("DEBUG cloud_loaded:", st.session_state.get("cloud_loaded"))
        st.write("DEBUG ls_read_tries:", st.session_state.get("ls_read_tries", 0))
        st.write("DEBUG ls_loaded_len:", st.session_state.get("ls_loaded_len", 0))
        st.write("DEBUG last_saved_len:", st.session_state.get("last_saved_len", 0))
        st.write("DEBUG last_save_ack:", st.session_state.get("last_save_ack", None))
        st.write("DEBUG ls_probe_len:", st.session_state.get("ls_probe_len", 0))
        st.write("DEBUG ls_last_error:", st.session_state.get("ls_last_error", ""))

        if st.session_state.events:
            st.write("DEBUG last event:", ev_to_dict(st.session_state.events[-1]))

        st.write("DEBUG seating:", s.seat_players)
        st.write("DEBUG players:", s.players)
        st.write("DEBUG steps (last 30):")
        st.code("\n".join(debug_steps[-30:]))


def page_overview(s: Settings):
    try_restore_from_local_storage()

    st.header("📊 數據總覽")
    ledger_df, sum_df, stats_df, rw, ds, dr, d_acc, _ = compute_game_state(s, st.session_state.events)
    merged = pd.merge(sum_df, stats_df, on="玩家", how="left")

    st.subheader("本局：總分 + 行為統計")
    st.dataframe(merged, hide_index=True, use_container_width=True)
    st.info(f"本局目前：{WINDS[rw]}{ds+1}局 (連{dr}) ｜ 累計東錢：${int(d_acc)}")

    if not ledger_df.empty:
        chart_df = ledger_df.set_index("#")[s.players]
        st.line_chart(chart_df)
        st.dataframe(ledger_df, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("已結束的牌局（封存，本機保存）")

    if not st.session_state.sessions:
        st.caption("尚無封存的牌局。你可以在「牌局錄入」按『結束牌局』。")
        return

    summary_rows = []
    for i, sess in enumerate(st.session_state.sessions, start=1):
        row = {"#": i, "結束時間": sess["ended_at"], "事件數": sess["event_count"], "本場東錢": sess.get("dong_total", 0)}
        for r in sess["sum_df"]:
            row[r["玩家"]] = r["總分"]
        summary_rows.append(row)

    st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)


# ============================
# 6) App
# ============================
def main():
    st.set_page_config(layout="wide", page_title="麻將計分系統")
    init_state()

    s: Settings = st.session_state.settings

    st.sidebar.title("選單")
    st.sidebar.caption(f"版本：{APP_VERSION}")
    st.sidebar.caption("✅ 本機暫存：延遲載入（避免 Streamlit Cloud init 階段出錯）")

    page = st.sidebar.radio("導航", ["設定", "牌局錄入", "數據總覽"], index=1)

    if page == "設定":
        page_settings(s)
    elif page == "牌局錄入":
        page_record(s)
    else:
        page_overview(s)


if __name__ == "__main__":
    main()
