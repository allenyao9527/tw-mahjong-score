# mahjong_score.py
import json
import os
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

APP_VERSION = "v2026-02-22_safe_6_mahjong_session"
WINDS = ["東", "南", "西", "北"]

SUPABASE_TABLE = "game_states"  # public.game_states
LOCAL_SAVES_DIR = "local_saves"


def local_save_state(gid: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
    """Save state to local file."""
    try:
        os.makedirs(LOCAL_SAVES_DIR, exist_ok=True)
        safe_gid = "".join(c for c in str(gid) if c.isalnum() or c in "_-") or "default"
        path = os.path.join(LOCAL_SAVES_DIR, f"{safe_gid}.json")
        rec = {"state": payload, "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=0)
        return True, "已存到本地"
    except Exception as e:
        return False, f"寫入本地失敗：{type(e).__name__}"


def local_load_latest(gid: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Load latest state from local file."""
    try:
        safe_gid = "".join(c for c in str(gid) if c.isalnum() or c in "_-") or "default"
        path = os.path.join(LOCAL_SAVES_DIR, f"{safe_gid}.json")
        if not os.path.isfile(path):
            return True, "本地沒有找到資料（這是新局）", None
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        data = rec.get("state") if isinstance(rec, dict) else None
        if not isinstance(data, dict):
            return False, "本地資料格式錯誤", None
        return True, "已從本地載入最新狀態", data
    except Exception as e:
        return False, f"讀取本地失敗：{type(e).__name__}", None


def local_list_recent(limit: int = 10) -> List[Tuple[str, str]]:
    """Return recent distinct game_ids from local saves."""
    try:
        if not os.path.isdir(LOCAL_SAVES_DIR):
            return []
        out: List[Tuple[str, str]] = []
        for fn in os.listdir(LOCAL_SAVES_DIR):
            if not fn.endswith(".json"):
                continue
            gid = fn[:-5]
            path = os.path.join(LOCAL_SAVES_DIR, fn)
            try:
                mtime = os.path.getmtime(path)
                ts = datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                ts = ""
            out.append((gid, ts))
        out.sort(key=lambda x: x[1] or "", reverse=True)
        return out[: int(limit)]
    except Exception:
        return []


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
        "hand_active": st.session_state.get("hand_active", False),
        "hand_started_at": st.session_state.get("hand_started_at"),
        "seat_locked": st.session_state.get("seat_locked", False),
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
    if "hand_active" in data:
        ha = bool(data["hand_active"])
        st.session_state["hand_active"] = ha
        st.session_state["seat_locked"] = ha  # 與 hand_active 同步
    elif "seat_locked" in data:
        st.session_state["seat_locked"] = bool(data["seat_locked"])
    if "hand_started_at" in data:
        st.session_state["hand_started_at"] = data["hand_started_at"]


def supabase_load_latest(game_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Load latest state from Supabase for this game_id."""
    sb = st.session_state.get("sb_client")
    if sb is None:
        return local_load_latest(game_id)

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
    payload = snapshot_state()
    if sb is None:
        return local_save_state(game_id, payload)

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
        return local_list_recent(limit=limit)

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
    st.session_state.setdefault("selected_pid", None)
    st.session_state.setdefault("seat_locked", False)  # 與 hand_active 同步
    st.session_state.setdefault("hand_active", False)  # 本將是否開始
    st.session_state.setdefault("hand_started_at", None)  # 可選：開始本將時間
    _players = st.session_state.get("settings", Settings()).players
    st.session_state.setdefault("scores_by_player", {p: 0 for p in _players})
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


def _all_events_for_daily_total() -> List[Dict[str, Any]]:
    """合併所有 sessions 的 events + 本將 events，供當天累計總分計算。"""
    sessions = st.session_state.get("sessions", [])
    current = st.session_state.get("events", [])
    merged: List[Dict[str, Any]] = []
    for sess in sessions:
        evs = sess.get("events", [])
        merged.extend(normalize_events(evs) if evs else [])
    merged.extend(normalize_events(current))
    return merged


def compute_daily_total(settings: Settings) -> pd.DataFrame:
    """當天累計總分：所有 sessions + 本將 events 合併計算。"""
    all_ev = _all_events_for_daily_total()
    _, sum_df, _, _, _, _, _, _ = compute_game_state(settings, all_ev)
    return sum_df


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
                    auto_bonus = bool(getattr(settings, "auto_dealer_bonus", True))
                    eff_tai = tai + bonus if auto_bonus else tai
                    A_dealer = amount_A(settings, eff_tai)
                    desc = f"{names[w]} 自摸({tai}+{bonus}台) [莊]" if auto_bonus else f"{names[w]} 自摸({tai}台) [莊]"
                    for p in range(n):
                        if p == w:
                            delta[p] += 3 * A_dealer
                        else:
                            delta[p] -= A_dealer
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

            elif result in ("放槍", "胡牌"):
                if w == l:
                    desc = "錯誤：贏家與輸家不能相同"
                else:
                    if 0 <= w < n:
                        stats[w]["胡"] += 1
                    if 0 <= l < n:
                        stats[l]["放槍"] += 1

                    if w == dealer_pid:
                        auto_bonus = bool(getattr(settings, "auto_dealer_bonus", True))
                        eff_tai = tai + bonus if auto_bonus else tai
                        A_dealer = amount_A(settings, eff_tai)
                        desc = f"{names[w]} 胡 {names[l]}({tai}+{bonus}台) [莊]" if auto_bonus else f"{names[w]} 胡 {names[l]}({tai}台) [莊]"
                        delta[w] += A_dealer
                        delta[l] -= A_dealer
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
        for k in ("record_hand_res", "hand_res"):
            st.session_state[k] = "自摸"
        for k in ("record_hand_tai", "hand_tai"):
            st.session_state[k] = 0
        for k in ("record_hand_win", "hand_win"):
            st.session_state[k] = 0
        for k in ("record_hand_lose", "hand_lose"):
            st.session_state[k] = 0
        st.session_state["reset_hand_inputs"] = False

    if st.session_state.get("reset_pen_inputs"):
        for k in ("record_pen_pt", "pen_pt"):
            st.session_state[k] = "詐胡"
        for k in ("record_pen_off", "pen_off"):
            st.session_state[k] = 0
        for k in ("record_pen_vic", "pen_vic"):
            st.session_state[k] = 0
        st.session_state["record_pen_amt"] = int(st.session_state.settings.base)
        st.session_state["pen_amt"] = int(st.session_state.settings.base)
        st.session_state["reset_pen_inputs"] = False


# ============================
# 5) UI
# ============================
def page_settings(s: Settings):
    st.header("⚙️ 設定")
    st.caption(f"版本：{APP_VERSION}")

    with st.form(key="set_main_form"):
        cols = st.columns(4)
        new_players = [cols[i].text_input(f"玩家{i+1}", value=s.players[i], key=f"set_player_{i}") for i in range(4)]

        st.divider()
        c1, c2 = st.columns(2)
        base = c1.number_input("底", min_value=0, value=int(s.base), step=50, key="set_base")
        tai_value = c2.number_input("每台金額", min_value=0, value=int(s.tai_value), step=10, key="set_tai_value")

        st.divider()
        cT1, cT2 = st.columns(2)
        draw_keep = cT1.toggle("流局連莊", value=bool(s.draw_keeps_dealer), key="set_draw_keep")
        auto_bonus = cT2.toggle("莊家加台自動計算", value=bool(getattr(s, "auto_dealer_bonus", True)), help="開啟後：台數只填牌型台；遇到莊家/連莊相關情境會自動加上莊連台。", key="set_auto_bonus")

        st.divider()
        st.subheader("東（可選）")
        host = st.selectbox(
            "場主(東錢收款者)",
            options=[0, 1, 2, 3],
            index=int(s.host_player_id),
            format_func=lambda pid: new_players[pid],
            key="set_host",
        )
        c3, c4 = st.columns(2)
        dong_x = c3.number_input("自摸扣東（每次）", min_value=0, value=int(s.dong_per_self_draw), step=10, key="set_dong_per")
        dong_cap = c4.number_input("東錢上限（累計）", min_value=0, value=int(s.dong_cap_total), step=50, key="set_dong_cap")

        save = st.form_submit_button("💾 儲存設定", use_container_width=True)

    if save:
        s.players = new_players
        s.base = int(base)
        s.tai_value = int(tai_value)
        s.draw_keeps_dealer = bool(draw_keep)
        s.auto_dealer_bonus = bool(auto_bonus)
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


def _build_scores_view(s: Settings, daily_sum_df: pd.DataFrame) -> Tuple[Dict[str, str], List[int]]:
    """從 daily_sum_df 更新 scores_by_player，產生 seat_map 與 scores_view_by_seat（顯示用，不寫回結算）。"""
    scores_by_player = {p: 0 for p in s.players}
    if daily_sum_df is not None and not daily_sum_df.empty:
        for _, row in daily_sum_df.iterrows():
            p = row.get("玩家")
            if p in scores_by_player:
                scores_by_player[p] = int(row.get("總分", 0))
    st.session_state["scores_by_player"] = scores_by_player
    seat_map = {WINDS[i]: s.players[s.seat_players[i]] for i in range(4)}
    scores_view_by_seat = [
        scores_by_player.get(seat_map["東"], 0),
        scores_by_player.get(seat_map["南"], 0),
        scores_by_player.get(seat_map["西"], 0),
        scores_by_player.get(seat_map["北"], 0),
    ]
    return seat_map, scores_view_by_seat


def render_seat_map(s: Settings, sum_df: pd.DataFrame, dealer_seat: int, daily_sum_df: Optional[pd.DataFrame] = None, scores_view_by_seat: Optional[List[int]] = None):
    """sum_df=本將分數，daily_sum_df=當天累計總分。scores_view_by_seat 提供時以「分數跟人走」顯示。"""
    def seat_btn(seat_idx: int, container):
        pid = s.seat_players[seat_idx]
        name = s.players[pid]
        if scores_view_by_seat is not None:
            score = scores_view_by_seat[seat_idx]
        else:
            display_df = daily_sum_df if daily_sum_df is not None and not daily_sum_df.empty else sum_df
            score = int(display_df.loc[display_df["玩家"] == name, "總分"].values[0]) if not display_df.empty else 0
        is_dealer = (seat_idx == dealer_seat)
        mark = " 🀄" if is_dealer else ""
        prefix = "👉 " if st.session_state.selected_seat == seat_idx else ""
        label = f"{prefix}{WINDS[seat_idx]}：{name}{mark} (${score})"

        if container.button(label, key=f"record_seatbtn_{seat_idx}", use_container_width=True):
            seat_locked = bool(st.session_state.get("seat_locked", False))

            if seat_locked:
                # 僅選取玩家/座位（顯示快速輸入面板），不交換
                if st.session_state.selected_seat == seat_idx:
                    st.session_state.selected_seat = None
                    st.session_state.selected_pid = None  # 點同一人取消選取
                else:
                    st.session_state.selected_seat = seat_idx
                    st.session_state.selected_pid = s.seat_players[seat_idx]
            else:
                # 交換座位模式
                if st.session_state.selected_seat is None:
                    st.session_state.selected_seat = seat_idx
                    st.session_state.selected_pid = s.seat_players[seat_idx]
                else:
                    o = st.session_state.selected_seat
                    s.seat_players[o], s.seat_players[seat_idx] = s.seat_players[seat_idx], s.seat_players[o]
                    st.session_state.selected_seat = None
                    st.session_state.selected_pid = None
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
        "events": [ev_to_dict(e) for e in events],  # 供當天累計總分合併計算
        "sum_df": sum_df.to_dict(orient="records"),
        "stats_df": stats_df.to_dict(orient="records"),
        "ledger_tail": ledger_df.tail(20).to_dict(orient="records"),
    }
    st.session_state.sessions.append(session)

    st.session_state.events = []
    st.session_state["selected_seat"] = None
    st.session_state["seat_locked"] = False
    st.session_state["hand_active"] = False
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
    st.session_state["hand_active"] = False
    st.session_state["seat_locked"] = False
    st.session_state["reset_hand_inputs"] = True
    st.session_state["reset_pen_inputs"] = True
    st.session_state.cloud_loaded = True
    supabase_save(st.session_state.game_id)
    st.rerun()


def page_record(s: Settings):
    st.header("🀄 牌局錄入")

    _apply_reset_flags_before_widgets()

    ledger_df, sum_df, stats_df, rw, ds, dr, d_acc, debug_steps = compute_game_state(s, st.session_state.events)
    daily_sum_df = compute_daily_total(s)

    mj_active = bool(st.session_state.get("hand_active", False))

    # ---------- C: 開始本將 / 結束本將（座位區塊上面） ----------
    c_start, c_end, c_sp = st.columns([1, 1, 2])
    with c_start:
        if not mj_active and st.button("✅ 開始本將", use_container_width=True, key="record_btn_start_mahjong"):
            st.session_state["hand_active"] = True
            st.session_state["seat_locked"] = True
            st.session_state["hand_started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state["selected_seat"] = None
            supabase_save(st.session_state.game_id)
            st.rerun()
    with c_end:
        if mj_active and st.button("🏁 結束本將", use_container_width=True, key="record_btn_end_mahjong"):
            if len(st.session_state.events) == 0:
                st.warning("本將尚無事件，無需結束。")
            else:
                end_current_session(s)
                st.success("已結束本將，事件已封存；當天累計總分保留。")
                st.rerun()

    st.subheader(f"目前局數：{WINDS[rw]}{ds+1}局 (連{dr})")
    lock_note = "｜本將進行中（座位已鎖）" if mj_active else ("｜座位已鎖定" if st.session_state.get("seat_locked", False) else "")
    st.caption("莊家依局數固定：東→南→西→北（只能調整玩家座位，不可手動改莊位）。" + lock_note)

    st.divider()
    seat_map, scores_view_by_seat = _build_scores_view(s, daily_sum_df)
    render_seat_map(s, sum_df, dealer_seat=ds, daily_sum_df=daily_sum_df, scores_view_by_seat=scores_view_by_seat)

    with st.expander("DEBUG Scores Mapping", expanded=False):
        gid = st.session_state.get("game_id", "")
        scores_bp = st.session_state.get("scores_by_player", {})
        st.write("gid:", gid)
        st.write("seat_map:", seat_map)
        st.write("scores_by_player:", scores_bp)
        st.write("scores_view_by_seat:", scores_view_by_seat)

    # ---------- B: 快速輸入面板（座位區塊下方，固定不往下滑） ----------
    qp_container = st.container()
    with qp_container:
        sel_seat = st.session_state.get("selected_seat")
        if sel_seat is not None:
            pid = s.seat_players[sel_seat]
            st.caption(f"快速輸入（已選 {s.players[pid]}）")
            qp_res = st.selectbox("結果", ["自摸", "胡牌", "流局"], key=f"qp_res_{pid}")
            qp_tai = 0
            if qp_res in ("自摸", "胡牌"):
                qp_tai = st.number_input("台數", min_value=0, step=1, key=f"qp_tai_{pid}")
            qp_win = pid
            qp_lose = 0
            if qp_res in ("自摸", "胡牌"):
                qp_win = st.selectbox("贏家", [0, 1, 2, 3], index=pid, format_func=lambda x: s.players[x], key=f"qp_win_{pid}")
            if qp_res == "胡牌":
                lose_opts = [p for p in [0, 1, 2, 3] if p != int(qp_win)]
                qp_lose = st.selectbox("輸家", lose_opts, format_func=lambda x: s.players[x], key=f"qp_lose_{pid}")
            if st.button("✅ 提交", use_container_width=True, key=f"qp_submit_{pid}"):
                if qp_res == "胡牌" and int(qp_win) == int(qp_lose):
                    st.error("胡牌時：贏家與輸家不能相同")
                else:
                    ev: Dict[str, Any] = {
                        "_type": "hand",
                        "result": "放槍" if qp_res == "胡牌" else qp_res,
                        "winner_id": int(qp_win) if qp_res in ("自摸", "胡牌") else None,
                        "loser_id": int(qp_lose) if qp_res == "胡牌" else None,
                        "tai": int(qp_tai) if qp_res in ("自摸", "胡牌") else 0,
                    }
                    st.session_state.events.append(ev)
                    st.session_state["selected_seat"] = None
                    st.session_state["reset_hand_inputs"] = True
                    supabase_save(st.session_state.game_id)
                    st.rerun()

    st.divider()
    # 🔒 座位鎖定（避免手機誤觸換位；本將進行中時座位由開始本將鎖定）
    lock_label = "🔒 鎖定座位（避免誤觸換位）" if not st.session_state.get("seat_locked", False) else "🔓 解鎖座位（可換位）"
    if mj_active:
        st.caption("本將進行中：座位已鎖定，請先『結束本將』才能換位。")
    if st.button(lock_label, use_container_width=True, key="record_btn_toggle_seat_lock", disabled=mj_active):
        if not mj_active:
            st.session_state["seat_locked"] = not bool(st.session_state.get("seat_locked", False))
            st.session_state["selected_seat"] = None
            supabase_save(st.session_state.game_id)
            st.rerun()

    if st.session_state.get("seat_locked", False) and not mj_active:
        st.caption("✅ 目前座位已鎖定；如要換位請先按『解鎖座位』。")

    st.divider()
    mode = st.radio("輸入類型", ["一般", "罰則"], horizontal=True, key="record_mode_radio")

    if mode == "一般":
        if st.session_state.get("record_hand_res") == "放槍":
            st.session_state["record_hand_res"] = "胡牌"
        res = st.selectbox("結果", ["自摸", "胡牌", "流局"], key="record_hand_res")

        # ✅ 流局不需要台數
        tai = 0
        if res in ("自摸", "胡牌"):
            tai = st.number_input("台數", min_value=0, step=1, key="record_hand_tai")
        else:
            st.session_state["hand_tai"] = 0

        win = 0
        lose = 0

        if res in ("自摸", "胡牌"):
            win = st.selectbox("贏家", [0, 1, 2, 3], format_func=lambda x: s.players[x], key="record_hand_win")

        # ✅ 胡牌時輸家下拉排除贏家
        if res == "胡牌":
            lose_options = [p for p in [0, 1, 2, 3] if p != int(win)]
            cur_lose = st.session_state.get("record_hand_lose", st.session_state.get("hand_lose", 0))
            if cur_lose == int(win):
                st.session_state["record_hand_lose"] = lose_options[0]
            lose = st.selectbox("輸家", lose_options, format_func=lambda x: s.players[x], key="record_hand_lose")

        submit = st.button("✅ 提交結果", use_container_width=True, key="record_btn_submit_hand")
        if submit:
            if res == "胡牌" and int(win) == int(lose):
                st.error("胡牌時：贏家與輸家不能相同")
            else:
                ev: Dict[str, Any] = {
                    "_type": "hand",
                    "result": "放槍" if res == "胡牌" else res,
                    "winner_id": int(win) if res in ("自摸", "胡牌") else None,
                    "loser_id": int(lose) if res == "胡牌" else None,
                }
                if res in ("自摸", "胡牌"):
                    ev["tai"] = int(tai)

                st.session_state.events.append(ev)
                st.session_state["reset_hand_inputs"] = True

                supabase_save(st.session_state.game_id)
                st.rerun()

    else:
        pt = st.selectbox("種類", ["詐胡", "詐摸"], key="record_pen_pt")
        off = st.selectbox("違規者", [0, 1, 2, 3], format_func=lambda x: s.players[x], key="record_pen_off")

        vic = 0
        if pt == "詐胡":
            vic = st.selectbox("賠付對象", [0, 1, 2, 3], format_func=lambda x: s.players[x], key="record_pen_vic")

        amt = st.number_input("金額", min_value=0, step=50, key="record_pen_amt")

        submit = st.button("🚨 提交罰則", use_container_width=True, key="record_btn_submit_pen")
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
    if c1.button("🔙 撤銷上一筆", use_container_width=True, key="record_btn_undo"):
        if st.session_state.events:
            st.session_state.events.pop()
            supabase_save(st.session_state.game_id)
            st.rerun()
    if c2.button("🧹 清空事件（只清本局事件）", use_container_width=True, key="record_btn_clear_events"):
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
    st.session_state.debug = st.toggle("顯示 Debug", value=bool(st.session_state.debug), key="record_debug_toggle")
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

    st.divider()
    with st.expander("☁️ 雲端存檔 / 開新局 / 封存（放在頁面底部）", expanded=False):
        cA, cB, cC = st.columns([1, 1, 1])

        if cA.button("💾 立即存檔到雲端", use_container_width=True, key="cloud_save_bottom"):
            ok, msg = supabase_save(st.session_state.game_id)
            if ok:
                st.success("已存到雲端 ✅")
            else:
                st.error(msg)

        if cB.button("🔄 從雲端重新載入", use_container_width=True, key="cloud_reload_bottom"):
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
            if st.button("🆕 開新局（換 gid）", use_container_width=True, key="cloud_newgid_bottom"):
                st.session_state["confirm_new_game"] = True

        if st.session_state.get("confirm_new_game"):
            st.warning("你確定要開新局嗎？（會清空目前畫面資料，但雲端歷史仍在舊 gid）")
            x1, x2 = st.columns(2)
            if x1.button("✅ 確定開新局", use_container_width=True, key="cloud_newgid_confirm"):
                st.session_state["confirm_new_game"] = False
                _new_game_confirmed()
            if x2.button("取消", use_container_width=True, key="cloud_newgid_cancel"):
                st.session_state["confirm_new_game"] = False

        st.info(f"🆔 本局 game_id：`{st.session_state.game_id}`（URL 會帶 gid，重整不會變）")

        b1, b2, b3 = st.columns([1, 1, 1])
        if b1.button("🏁 結束牌局（封存並新開）", use_container_width=True, key="cloud_end_session_bottom"):
            if len(st.session_state.events) == 0:
                st.warning("目前沒有事件，無需結束。")
            else:
                end_current_session(s)
                st.success("已封存本局並開始新局（雲端已保存）。")
                st.rerun()

        if b2.button("🧹 清空本局（保留封存）", use_container_width=True, key="cloud_clear_current_bottom"):
            st.session_state.events = []
            st.session_state["reset_hand_inputs"] = True
            st.session_state["reset_pen_inputs"] = True
            # 快速面板/鎖座位狀態也一併重置，避免下一將混亂
            st.session_state.seat_locked = False
            st.session_state.quick_actor_seat = None
            st.session_state.quick_action = None
            supabase_save(st.session_state.game_id)
            st.rerun()

        if b3.button("🗑️ 清空全部（本局+封存）", use_container_width=True, key="cloud_clear_all_bottom"):
            st.session_state.events = []
            st.session_state.sessions = []
            st.session_state.selected_seat = None
            st.session_state["hand_active"] = False
            st.session_state["reset_hand_inputs"] = True
            st.session_state["reset_pen_inputs"] = True
            st.session_state.seat_locked = False
            st.session_state.quick_actor_seat = None
            st.session_state.quick_action = None
            supabase_save(st.session_state.game_id)
            st.rerun()


def page_overview(s: Settings):
    st.header("📊 數據總覽")

    ledger_df, sum_df, stats_df, rw, ds, dr, d_acc, _ = compute_game_state(s, st.session_state.events)
    daily_sum_df = compute_daily_total(s)
    merged = pd.merge(sum_df, stats_df, on="玩家", how="left")
    seat_map, scores_view_by_seat = _build_scores_view(s, daily_sum_df)

    with st.expander("DEBUG Scores Mapping", expanded=False):
        gid = st.session_state.get("game_id", "")
        scores_bp = st.session_state.get("scores_by_player", {})
        st.write("gid:", gid)
        st.write("seat_map:", seat_map)
        st.write("scores_by_player:", scores_bp)
        st.write("scores_view_by_seat:", scores_view_by_seat)

    st.subheader("當天累計總分（sessions + 本將）")
    st.dataframe(daily_sum_df, hide_index=True, use_container_width=True)

    st.subheader("本局：總分 + 行為統計")
    st.dataframe(merged, hide_index=True, use_container_width=True)
    st.info(f"本局目前：{WINDS[rw]}{ds+1}局 (連{dr}) ｜ 累計東錢：${int(d_acc)}")

    if not ledger_df.empty:
        chart_df = ledger_df.set_index("#")[s.players]
        st.line_chart(chart_df)
        st.dataframe(ledger_df, hide_index=True, use_container_width=True)

    overview_df = ledger_df
    with st.expander("DEBUG Overview", expanded=False):
        if overview_df.empty:
            st.write("overview_df is empty")
        else:
            distinct_hand_index = sorted(overview_df["類型"].unique()) if "類型" in overview_df.columns else []
            max_hand_index = overview_df["#"].max() if "#" in overview_df.columns else None
            st.write("distinct_hand_index (sorted unique):", distinct_hand_index)
            st.write("max_hand_index:", max_hand_index)
            st.write("len(distinct_hand_index):", len(distinct_hand_index))
            st.write("overview_df.shape:", overview_df.shape)
            st.write("overview_df.tail(5):")
            st.dataframe(overview_df.tail(5), hide_index=True)

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
        step=1,
        key="overview_sess_idx",
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
    new_mobile_on = st.sidebar.toggle("📱 手機直式座位（東南西北）", value=mobile_on, key="sidebar_mobile_toggle")
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
            if st.button("切換", use_container_width=True, key="sidebar_btn_switch_gid"):
                switch_to_game_id(pick)

    page = st.sidebar.radio("導航", ["設定", "牌局錄入", "數據總覽"], index=1, key="nav_radio")

    if page == "設定":
        page_settings(s)
    elif page == "牌局錄入":
        page_record(s)
    else:
        page_overview(s)


if __name__ == "__main__":
    main()