# ============================
# mahjong_score.py
# Professional Match Flow Version
# ============================

import json
import uuid
import random
from datetime import datetime
from dataclasses import dataclass, field, asdict, is_dataclass
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client = None
    Client = None  # type: ignore

APP_VERSION = "v2026-02-Professional_Match_Flow"
WINDS = ["東", "南", "西", "北"]
SUPABASE_TABLE = "game_states"

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
# 2) Supabase
# ============================

def _get_supabase_client():
    if create_client is None:
        return None
    try:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", "")
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception:
        return None


def _get_or_init_game_id():
    try:
        gid = st.query_params.get("gid", "")
        if gid:
            return str(gid)
    except Exception:
        pass

    gid = uuid.uuid4().hex
    try:
        st.query_params["gid"] = gid
    except Exception:
        pass
    return gid


def snapshot_state():
    s = st.session_state.settings
    settings_dict = asdict(s) if is_dataclass(s) else dict(s)
    return {
        "version": APP_VERSION,
        "saved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "settings": settings_dict,
        "events": st.session_state.get("events", []),
        "sessions": st.session_state.get("sessions", []),
        "phase": st.session_state.get("phase", "seat_confirm"),
    }


def restore_state(data):
    if not isinstance(data, dict):
        return
    if isinstance(data.get("settings"), dict):
        try:
            st.session_state.settings = Settings(**data["settings"])
        except Exception:
            st.session_state.settings = Settings()
    st.session_state.events = data.get("events", []) or []
    st.session_state.sessions = data.get("sessions", []) or []
    st.session_state.phase = data.get("phase", "seat_confirm")


def supabase_load_latest(game_id):
    sb = st.session_state.get("sb_client")
    if sb is None:
        return False, "", None
    try:
        res = (
            sb.table(SUPABASE_TABLE)
            .select("state, created_at")
            .eq("game_id", game_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None)
        if not rows:
            return True, "新局", None
        row = rows[0]
        state = row.get("state")
        if isinstance(state, str):
            data = json.loads(state)
        else:
            data = state
        return True, "已載入", data
    except Exception as e:
        return False, str(e), None


def supabase_save(game_id):
    sb = st.session_state.get("sb_client")
    if sb is None:
        return False, ""
    payload = snapshot_state()
    try:
        sb.table(SUPABASE_TABLE).insert(
            {"game_id": game_id, "state": payload}
        ).execute()
        return True, ""
    except Exception as e:
        return False, str(e)


# ============================
# 3) Init
# ============================

def init_state():
    st.session_state.setdefault("settings", Settings())
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("sessions", [])
    st.session_state.setdefault("selected_seat", None)
    st.session_state.setdefault("phase", "seat_confirm")
    st.session_state.setdefault("game_id", _get_or_init_game_id())
    st.session_state.setdefault("sb_client", _get_supabase_client())
    st.session_state.setdefault("cloud_loaded", False)

    if not st.session_state.cloud_loaded:
        ok, msg, data = supabase_load_latest(st.session_state.game_id)
        if ok and data:
            restore_state(data)
        st.session_state.cloud_loaded = True


# ============================
# 4) Core Compute (原邏輯保留)
# ============================

def compute_game_state(settings, events_raw):
    n = 4
    names = settings.players
    seat_players = settings.seat_players
    cum = [0] * n
    rows = []

    rw, ds, dr, d_acc = 0, 0, 0, 0

    def advance_dealer():
        nonlocal rw, ds, dr
        ds = (ds + 1) % 4
        dr = 0
        if ds == 0:
            rw = (rw + 1) % 4

    for idx, ev in enumerate(events_raw, start=1):
        delta = [0] * n
        dealer_pid = seat_players[ds]

        if ev["_type"] == "hand":
            result = ev["result"]
            w = ev["winner_id"]
            l = ev["loser_id"]
            tai = ev["tai"]
            A = settings.base + tai * settings.tai_value

            if result == "流局":
                dr += 1

            elif result == "自摸":
                if w == dealer_pid:
                    for p in range(n):
                        if p == w:
                            delta[p] += 3 * A
                        else:
                            delta[p] -= A
                    dr += 1
                else:
                    dealer_pay = settings.base + (tai + (1 + 2 * dr)) * settings.tai_value
                    for p in range(n):
                        if p == w:
                            delta[p] += dealer_pay + 2 * A
                        elif p == dealer_pid:
                            delta[p] -= dealer_pay
                        else:
                            delta[p] -= A
                    advance_dealer()

            elif result == "放槍":
                if w == dealer_pid:
                    delta[w] += A
                    delta[l] -= A
                    dr += 1
                else:
                    delta[w] += A
                    delta[l] -= A
                    advance_dealer()

        for p in range(n):
            cum[p] += delta[p]

        row = {"#": idx}
        for p in range(n):
            row[names[p]] = cum[p]
        rows.append(row)

    ledger_df = pd.DataFrame(rows)
    sum_df = pd.DataFrame(
        [{"玩家": names[i], "總分": cum[i]} for i in range(n)]
    )

    return ledger_df, sum_df, rw, ds, dr


# ============================
# 5) UI
# ============================

def render_seat_map(s, sum_df, dealer_seat):
    def seat_btn(seat_idx, container):
        pid = s.seat_players[seat_idx]
        name = s.players[pid]
        mark = " 🀄" if seat_idx == dealer_seat else ""
        label = f"{WINDS[seat_idx]}：{name}{mark}"

        if container.button(label, key=f"seatbtn_{seat_idx}", use_container_width=True):
            if st.session_state.phase != "seat_confirm":
                st.warning("本將已開始，請先結束或回到座位確認")
                return
            if st.session_state.selected_seat is None:
                st.session_state.selected_seat = seat_idx
            else:
                o = st.session_state.selected_seat
                s.seat_players[o], s.seat_players[seat_idx] = (
                    s.seat_players[seat_idx],
                    s.seat_players[o],
                )
                st.session_state.selected_seat = None

    cols = st.columns(4)
    for i in range(4):
        seat_btn(i, cols[i])


def page_record(s):
    ledger_df, sum_df, rw, ds, dr = compute_game_state(
        s, st.session_state.events
    )

    phase = st.session_state.phase

    st.subheader(f"目前局數：{WINDS[rw]}{ds+1}局 (連{dr})")

    render_seat_map(s, sum_df, dealer_seat=ds)

    # ======================
    # 座位確認階段
    # ======================
    if phase == "seat_confirm":
        st.success("🀄 新的一將開始：請確認座位，確認後按開始本將")
        if st.button("▶ 開始本將", use_container_width=True):
            st.session_state.phase = "playing"
            supabase_save(st.session_state.game_id)
            st.rerun()
        st.stop()

    # ======================
    # 正式進行階段
    # ======================

    mode = st.radio("輸入類型", ["一般", "罰則"], horizontal=True)

    if mode == "一般":
        res = st.selectbox("結果", ["自摸", "放槍", "流局"])
        tai = 0
        if res in ("自摸", "放槍"):
            tai = st.number_input("台數", min_value=0, step=1)

        win = 0
        lose = 0

        if res in ("自摸", "放槍"):
            win = st.selectbox("贏家", [0, 1, 2, 3], format_func=lambda x: s.players[x])

        if res == "放槍":
            lose_options = [p for p in [0, 1, 2, 3] if p != int(win)]
            lose = st.selectbox("放槍家", lose_options, format_func=lambda x: s.players[x])

        if st.button("提交結果"):
            ev = {
                "_type": "hand",
                "result": res,
                "winner_id": win,
                "loser_id": lose,
                "tai": tai,
            }
            st.session_state.events.append(ev)
            supabase_save(st.session_state.game_id)
            st.rerun()

    if st.button("🏁 結束牌局（封存並新開）"):
        st.session_state.sessions.append(
            {
                "ended_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "sum_df": sum_df.to_dict("records"),
            }
        )
        st.session_state.events = []
        st.session_state.phase = "seat_confirm"
        supabase_save(st.session_state.game_id)
        st.rerun()

    if not ledger_df.empty:
        st.dataframe(ledger_df)


# ============================
# 6) App
# ============================

def main():
    st.set_page_config(layout="wide")
    init_state()
    s = st.session_state.settings
    page_record(s)


if __name__ == "__main__":
    main()
