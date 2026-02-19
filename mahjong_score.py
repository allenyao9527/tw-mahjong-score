# mahjong_score.py
import json
import uuid
from datetime import datetime
from dataclasses import dataclass, field, asdict, is_dataclass
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components  # for iPhone Safari localStorage

# Supabase
try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client = None
    Client = None  # type: ignore

APP_VERSION = "v2026-02-20_mobile_toggle_recent_fix_1"
WINDS = ["東", "南", "西", "北"]

SUPABASE_TABLE = "game_states"  # public.game_states


# ============================
# 1) Models
# ============================
@dataclass
class Settings:
    base: int = 300
    tai_value: int = 100

    # 預設玩家
    players: List[str] = field(default_factory=lambda: ["玩家1", "玩家2", "玩家3", "玩家4"])
    # seat_players[seat_idx] = player_id, seat_idx: 0=東 1=南 2=西 3=北
    seat_players: List[int] = field(default_factory=lambda: [0, 1, 2, 3])

    draw_keeps_dealer: bool = True

    # 東錢（可選）
    host_player_id: int = 0
    dong_per_self_draw: int = 0
    dong_cap_total: int = 0


# ============================
# 2) Supabase Bridge
# ============================
def _get_supabase_client() -> Optional["Client"]:
    """Create a Supabase client from Streamlit secrets."""
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


# --- ✅ GID persistence for iPhone Safari (1+2+3) ---
def _persist_gid_to_local_storage(gid: str) -> None:
    """Store gid in browser localStorage."""
    try:
        safe_gid = str(gid).replace('"', "").replace("'", "")
        components.html(
            f"""
            <script>
            try {{
              localStorage.setItem("tw_mj_last_gid", "{safe_gid}");
            }} catch (e) {{}}
            </script>
            """,
            height=0,
        )
    except Exception:
        pass


def _restore_gid_from_local_storage_if_missing() -> None:
    """
    If URL has no gid, restore from localStorage and redirect to ?gid=...
    (works on iPhone Safari normal mode; private mode may not persist)
    """
    try:
        components.html(
            """
            <script>
            (function() {
              try {
                const params = new URLSearchParams(window.location.search);
                const gid = params.get("gid");
                if (!gid) {
                  const last = localStorage.getItem("tw_mj_last_gid");
                  if (last && last.length > 0) {
                    params.set("gid", last);
                    const newUrl = window.location.pathname + "?" + params.toString();
                    window.location.replace(newUrl);
                  }
                }
              } catch (e) {}
            })();
            </script>
            """,
            height=0,
        )
    except Exception:
        pass


def _get_or_init_game_id() -> str:
    """
    Priority:
    1) Use URL query param gid if present (and persist to localStorage)
    2) If missing, try restore from localStorage by forcing a redirect (iPhone Safari)
    3) If still missing, generate a new gid and write back to query params + localStorage
    """
    # (2) If URL missing gid, try restore (may redirect)
    try:
        gid = st.query_params.get("gid", "")
        if not gid:
            _restore_gid_from_local_storage_if_missing()
    except Exception:
        pass

    # (1) Read again (after potential restore)
    try:
        gid = st.query_params.get("gid", "")
        if gid:
            gid = str(gid)
            _persist_gid_to_local_storage(gid)
            return gid
    except Exception:
        gid = ""

    # (3) Generate new
    gid = uuid.uuid4().hex
    try:
        st.query_params["gid"] = gid
    except Exception:
        pass
    _persist_gid_to_local_storage(gid)
    return gid


def snapshot_state() -> Dict[str, Any]:
    s = st.session_state.settings
    settings_dict = asdict(s) if is_dataclass(s) else dict(s)
    return {
        "version": APP_VERSION,
        "saved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "settings": settings_dict,
        "events": st.session_state.get("events", []),
        "sessions": st.session_state.get("sessions", []),
    }


def restore_state(data: Dict[str, Any]) -> None:
    if not data or not isinstance(data, dict):
        return
    if isinstance(data.get("settings"), dict):
        try:
            st.session_state.settings = Settings(**data["settings"])
        except Exception:
            st.session_state.settings = Settings()
    st.session_state.events = data.get("events", []) or []
    st.session_state.sessions = data.get("sessions", []) or []


def supabase_load_latest(game_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Load latest state from Supabase for this game_id."""
    sb = st.session_state.get("sb_client")
    if sb is None:
        return False, "Supabase 尚未連線（請在 Streamlit Cloud 設定 Secrets）", None

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
            return True, "雲端沒有找到資料（這是新局）", None

        row = rows[0]
        state = row.get("state")
        if isinstance(state, str):
            data = json.loads(state)
        else:
            data = state
        if not isinstance(data, dict):
            return False, "雲端資料格式錯誤", None
        return True, "已從雲端載入最新狀態", data

    except Exception as e:
        return False, f"讀取 Supabase 失敗：{type(e).__name__}", None


def supabase_save(game_id: str) -> Tuple[bool, str]:
    """Save current snapshot into Supabase (insert a new row each time)."""
    sb = st.session_state.get("sb_client")
    if sb is None:
        return False, "Supabase 尚未連線（請在 Streamlit Cloud 設定 Secrets）"

    payload = snapshot_state()
    try:
        _ = (
            sb.table(SUPABASE_TABLE)
            .insert({"game_id": game_id, "state": payload})
            .execute()
        )
        return True, "已存到雲端"
    except Exception as e:
        return False, f"寫入 Supabase 失敗：{type(e).__name__}"


# --- ✅ Recent games quick switch (Supabase last 10) ---
def supabase_list_recent_game_ids(limit: int = 10, scan_rows: int = 200) -> List[Tuple[str, str]]:
    """Return recent distinct game_ids with latest created_at (client-side dedupe)."""
    sb = st.session_state.get("sb_client")
    if sb is None:
        return []
    try:
        res = (
            sb.table(SUPABASE_TABLE)
            .select("game_id, created_at")
            .order("created_at", desc=True)
            .limit(int(scan_rows))
            .execute()
        )
        rows = getattr(res, "data", None) or []
        seen = set()
        out: List[Tuple[str, str]] = []
        for r in rows:
            gid = r.get("game_id")
            ts = r.get("created_at")
            if not gid or gid in seen:
                continue
            seen.add(gid)
            out.append((str(gid), str(ts) if ts else ""))
            if len(out) >= int(limit):
                break
        return out
    except Exception:
        return []


def switch_to_game_id(gid: str) -> None:
    """Switch current session to another gid by updating query params and forcing cloud reload."""
    gid = str(gid)
    try:
        st.query_params["gid"] = gid
    except Exception:
        pass
    st.session_state.game_id = gid
    st.session_state.cloud_loaded = False
    st.rerun()


# --- ✅ Mobile layout (stable toggle; no Safari auto-redirect) ---
def _is_mobile_layout() -> bool:
    try:
        return str(st.query_params.get("mobile", "")) == "1"
    except Exception:
        return False


def set_mobile_layout(enabled: bool) -> None:
    """
    Toggle ?mobile=1 in URL for stable layout.
    This is more reliable than JS auto-detect on iPhone Safari.
    """
    try:
        if enabled:
            st.query_params["mobile"] = "1"
        else:
            qp = dict(st.query_params)
            qp.pop("mobile", None)
            st.query_params.clear()
            for k, v in qp.items():
                st.query_params[k] = v
    except Exception:
        pass
    st.rerun()


# ============================
# 3) State / Helpers
# ============================
def init_state():
    st.session_state.setdefault("settings", Settings())
    st.session_state.setdefault("events", [])       # 當前牌局
    st.session_state.setdefault("sessions", [])     # 封存的牌局（同一個 game_id 下）

    st.session_state.setdefault("selected_seat", None)
    st.session_state.setdefault("debug", True)

    # UI state (reactive widgets keys)
    st.session_state.setdefault("hand_res", "自摸")
    st.session_state.setdefault("hand_tai", 0)
    st.session_state.setdefault("hand_win", 0)
    st.session_state.setdefault("hand_lose", 0)

    st.session_state.setdefault("pen_pt", "詐胡")
    st.session_state.setdefault("pen_off", 0)
    st.session_state.setdefault("pen_vic", 0)
    st.session_state.setdefault("pen_amt", 300)

    # reset flags
    st.session_state.setdefault("reset_hand_inputs", False)
    st.session_state.setdefault("reset_pen_inputs", False)

    # Supabase init
    st.session_state.setdefault("game_id", _get_or_init_game_id())
    st.session_state.setdefault("sb_client", _get_supabase_client())
    st.session_state.setdefault("cloud_loaded", False)

    # Load once
    if not st.session_state.cloud_loaded:
        ok, msg, data = supabase_load_latest(st.session_state.game_id)
        st.session_state["cloud_load_msg"] = msg
        if ok and data:
            restore_state(data)
        st.session_state.cloud_loaded = True


def safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def amount_A(settings: Settings, tai: int) -> int:
    return safe_int(settings.base) + safe_int(tai) * safe_int(settings.tai_value)


def dealer_bonus_tai(dealer_run: int) -> int:
    """
    上莊=1台, 連1=3台, 連2=5台, 連3=7台
    => bonus = 1 + 2*dealer_run
    """
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
            w = safe_int(ev.get("winner_id"), default=-1)
            l = safe_int(ev.get("loser_id"), default=-1)
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

                # 東錢（可選）
                if settings.dong_per_self_draw > 0 and settings.dong_cap_total > 0:
                    remain = max(0, int(settings.dong_cap_total) - int(d_acc))
                    take = min(int(settings.dong_per_self_draw), remain)
                    if take > 0 and 0 <= w < n:
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
        ok, msg = supabase_save(st.session_state.game_id)
        if ok:
            st.success("✅ 已儲存設定（雲端已保存）")
        else:
            st.warning(f"⚠️ 已儲存設定（但雲端保存失敗：{msg}）")
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
            supabase_save(st.session_state.game_id)
            st.rerun()

    # 📱 Mobile: vertical order 東南西北
    if _is_mobile_layout():
        seat_btn(0, st)  # 東
        seat_btn(1, st)  # 南
        seat_btn(2, st)  # 西
        seat_btn(3, st)  # 北
        return

    # 🖥 Desktop: cross layout
    top = st.columns([1, 1.5, 1])
    seat_btn(1, top[1])  # 南
    mid = st.columns([1, 1.5, 1])
    seat_btn(2, mid[0])  # 西
    seat_btn(0, mid[2])  # 東
    bot = st.columns([1, 1.5, 1])
    seat_btn(3, bot[1])  # 北


def end_current_session(s: Settings):
    """把目前 events 封存到 sessions，然後清空 events 開新局。"""
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

    supabase_save(st.session_state.game_id)


def _new_game_confirmed():
    new_gid = uuid.uuid4().hex
    try:
        st.query_params["gid"] = new_gid
    except Exception:
        pass

    st.session_state.game_id = new_gid
    st.session_state.settings = Settings()
    st.session_state.events = []
    st.session_state.sessions = []
    st.session_state.selected_seat = None
    st.session_state["reset_hand_inputs"] = True
    st.session_state["reset_pen_inputs"] = True
    st.session_state.cloud_loaded = True
    supabase_save(st.session_state.game_id)
    st.rerun()


def page_record(s: Settings):
    st.header("🀄 牌局錄入")

    _apply_reset_flags_before_widgets()

    ledger_df, sum_df, stats_df, rw, ds, dr, d_acc, debug_steps = compute_game_state(s, st.session_state.events)

    st.subheader(f"目前局數：{WINDS[rw]}{ds+1}局 (連{dr})")
    st.caption("莊家依局數固定：東→南→西→北（只能調整玩家座位，不可手動改莊位）。")

    st.divider()
    render_seat_map(s, sum_df, dealer_seat=ds)

    st.divider()

    # 雲端/局管理
    cA, cB, cC = st.columns([1, 1, 1])
    if cA.button("💾 立即存檔到雲端", use_container_width=True):
        ok, msg = supabase_save(st.session_state.game_id)
        if ok:
            st.success("已存到雲端 ✅")
        else:
            st.error(msg)

    if cB.button("🔄 從雲端重新載入", use_container_width=True):
        ok, msg, data = supabase_load_latest(st.session_state.game_id)
        if ok and data:
            restore_state(data)
            st.success("已從雲端載入 ✅")
            st.rerun()
        elif ok:
            st.warning("雲端沒有資料（新局）")
        else:
            st.error(msg)

    with cC:
        if st.button("🆕 開新局（換 gid）", use_container_width=True):
            st.session_state["confirm_new_game"] = True

    if st.session_state.get("confirm_new_game"):
        st.warning("你確定要開新局嗎？（會清空目前畫面資料，但雲端歷史仍在舊 gid）")
        x1, x2 = st.columns(2)
        if x1.button("✅ 確定開新局", use_container_width=True):
            st.session_state["confirm_new_game"] = False
            _new_game_confirmed()
        if x2.button("取消", use_container_width=True):
            st.session_state["confirm_new_game"] = False

    st.info(f"🆔 本局 game_id：`{st.session_state.game_id}`（URL 會帶 gid，重整不會變）")

    st.divider()

    # 牌局封存（同 gid 下）
    b1, b2, b3 = st.columns([1, 1, 1])
    if b1.button("🏁 結束牌局（封存並新開）", use_container_width=True):
        if len(st.session_state.events) == 0:
            st.warning("目前沒有事件，無需結束。")
        else:
            end_current_session(s)
            st.success("已封存本局並開始新局（雲端已保存）。")
            st.rerun()

    if b2.button("🧹 清空本局（保留封存）", use_container_width=True):
        st.session_state.events = []
        st.session_state["reset_hand_inputs"] = True
        st.session_state["reset_pen_inputs"] = True
        supabase_save(st.session_state.game_id)
        st.rerun()

    if b3.button("🗑️ 清空全部（本局+封存）", use_container_width=True):
        st.session_state.events = []
        st.session_state.sessions = []
        st.session_state.selected_seat = None
        st.session_state["reset_hand_inputs"] = True
        st.session_state["reset_pen_inputs"] = True
        supabase_save(st.session_state.game_id)
        st.rerun()

    mode = st.radio("輸入類型", ["一般", "罰則"], horizontal=True)

    if mode == "一般":
        res = st.selectbox("結果", ["自摸", "放槍", "流局"], key="hand_res")

        # ✅ 流局不需要台數
        tai = 0
        if res in ("自摸", "放槍"):
            tai = st.number_input("台數", min_value=0, step=1, key="hand_tai")
        else:
            st.session_state["hand_tai"] = 0

        win = 0
        lose = 0

        if res in ("自摸", "放槍"):
            win = st.selectbox("贏家", [0, 1, 2, 3], format_func=lambda x: s.players[x], key="hand_win")

        # ✅ 放槍輸家下拉排除贏家
        if res == "放槍":
            lose_options = [p for p in [0, 1, 2, 3] if p != int(win)]
            if st.session_state.get("hand_lose") == int(win):
                st.session_state["hand_lose"] = lose_options[0]
            lose = st.selectbox("放槍家", lose_options, format_func=lambda x: s.players[x], key="hand_lose")

        submit = st.button("✅ 提交結果", use_container_width=True)
        if submit:
            if res == "放槍" and int(win) == int(lose):
                st.error("放槍時：贏家與放槍家不能相同")
            else:
                ev: Dict[str, Any] = {
                    "_type": "hand",
                    "result": res,
                    "winner_id": int(win) if res in ("自摸", "放槍") else None,
                    "loser_id": int(lose) if res == "放槍" else None,
                }
                if res in ("自摸", "放槍"):
                    ev["tai"] = int(tai)

                st.session_state.events.append(ev)
                st.session_state["reset_hand_inputs"] = True

                supabase_save(st.session_state.game_id)
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
            ev = {
                "_type": "penalty",
                "p_type": pt,
                "offender_id": int(off),
                "victim_id": int(vic),
                "amount": int(amt),
            }
            st.session_state.events.append(ev)
            st.session_state["reset_pen_inputs"] = True

            supabase_save(st.session_state.game_id)
            st.rerun()

    c1, c2 = st.columns(2)
    if c1.button("🔙 撤銷上一筆", use_container_width=True):
        if st.session_state.events:
            st.session_state.events.pop()
            supabase_save(st.session_state.game_id)
            st.rerun()
    if c2.button("🧹 清空事件（只清本局事件）", use_container_width=True):
        st.session_state.events = []
        st.session_state["reset_hand_inputs"] = True
        st.session_state["reset_pen_inputs"] = True
        supabase_save(st.session_state.game_id)
        st.rerun()

    st.divider()
    st.info(f"💰 累計東錢：${int(d_acc)}（已算入總分）")

    if not ledger_df.empty:
        st.dataframe(ledger_df, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("DEBUG")
    st.session_state.debug = st.toggle("顯示 Debug", value=bool(st.session_state.debug))
    if st.session_state.debug:
        st.write("DEBUG cloud load msg:", st.session_state.get("cloud_load_msg", ""))
        st.write("DEBUG game_id:", st.session_state.game_id)
        st.write(f"DEBUG events len: {len(st.session_state.events)}")
        st.write("DEBUG sessions len:", len(st.session_state.sessions))
        if st.session_state.events:
            st.write("DEBUG last event:", ev_to_dict(st.session_state.events[-1]))
        st.write("DEBUG seating:", s.seat_players)
        st.write("DEBUG players:", s.players)
        st.write("DEBUG steps (last 30):")
        st.code("\n".join(debug_steps[-30:]))


def page_overview(s: Settings):
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
    st.subheader("已結束的牌局（封存，仍在同一個 gid）")

    if not st.session_state.sessions:
        st.caption("尚無封存的牌局。你可以在「牌局錄入」按『結束牌局』。")
        return

    summary_rows = []
    for i, sess in enumerate(st.session_state.sessions, start=1):
        row = {
            "#": i,
            "結束時間": sess["ended_at"],
            "事件數": sess["event_count"],
            "本場東錢": sess.get("dong_total", 0),
        }
        for r in sess["sum_df"]:
            row[r["玩家"]] = r["總分"]
        summary_rows.append(row)

    st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

    idx = st.number_input(
        "查看第幾場封存牌局（1=最早）",
        min_value=1,
        max_value=len(st.session_state.sessions),
        value=len(st.session_state.sessions),
        step=1
    )
    sess = st.session_state.sessions[int(idx) - 1]

    st.markdown("**該場：行為統計**")
    st.dataframe(pd.DataFrame(sess["stats_df"]), hide_index=True, use_container_width=True)

    st.markdown("**該場：最後 20 筆明細（尾巴）**")
    st.dataframe(pd.DataFrame(sess["ledger_tail"]), hide_index=True, use_container_width=True)


# ============================
# 6) App
# ============================
def main():
    st.set_page_config(layout="wide", page_title="麻將計分系統")
    init_state()

    s: Settings = st.session_state.settings

    st.sidebar.title("選單")
    st.sidebar.caption(f"版本：{APP_VERSION}")

    # ✅ stable mobile toggle (no Safari auto-redirect)
    mobile_on = _is_mobile_layout()
    new_mobile_on = st.sidebar.toggle("📱 手機直式座位（東南西北）", value=mobile_on)
    if new_mobile_on != mobile_on:
        set_mobile_layout(new_mobile_on)

    # Supabase status
    if st.session_state.get("sb_client") is None:
        st.sidebar.error("Supabase 未連線：請到 Streamlit Cloud → Settings → Secrets 設定 SUPABASE_URL / SUPABASE_KEY")
    else:
        st.sidebar.success("Supabase 已連線 ✅")

    # ✅ Enhancement: Recent games quick switch
    with st.sidebar.expander("🕘 近期牌局（最近10局）", expanded=False):
        recent = supabase_list_recent_game_ids(limit=10, scan_rows=200)
        if st.session_state.get("sb_client") is None:
            st.caption("Supabase 未連線")
        elif not recent:
            st.caption("尚無資料或抓取失敗")
        else:
            options = [gid for gid, _ in recent]

            def fmt(gid: str) -> str:
                ts = next((t for g, t in recent if g == gid), "")
                ts_short = ts[:19].replace("T", " ") if ts else ""
                mark = "（目前）" if gid == st.session_state.game_id else ""
                return f"{gid[:8]}  {ts_short} {mark}".strip()

            pick = st.selectbox(
                "切換到：",
                options=options,
                index=options.index(st.session_state.game_id) if st.session_state.game_id in options else 0,
                format_func=fmt,
                key="recent_gid_pick",
            )
            if st.button("切換", use_container_width=True):
                switch_to_game_id(pick)

    page = st.sidebar.radio("導航", ["設定", "牌局錄入", "數據總覽"], index=1)

    if page == "設定":
        page_settings(s)
    elif page == "牌局錄入":
        page_record(s)
    else:
        page_overview(s)


if __name__ == "__main__":
    main()
