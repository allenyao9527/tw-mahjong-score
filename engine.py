from typing import List, Dict, Any, Optional, Tuple
from dataclasses import asdict, is_dataclass
import pandas as pd

from models import Settings
from scoring import amount_A, dealer_bonus_tai

# 常數（僅用於顯示標籤）
WINDS = ["東", "南", "西", "北"]


def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def ev_to_dict(ev: Any) -> Dict[str, Any]:
    if isinstance(ev, dict):
        d = dict(ev)
    elif is_dataclass(ev):
        d = asdict(ev)
    else:
        d: Dict[str, Any] = {}
        for k in ("result", "winner_id", "loser_id", "tai", "p_type", "offender_id", "victim_id", "amount"):
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


def hand_label(rw_idx: int, dealer_seat: int) -> str:
    if rw_idx >= 4:
        return "本將結束"
    return f"{WINDS[min(rw_idx, 3)]}{dealer_seat + 1}局"


def compute_game_state(settings: Settings, events_raw: List[Any]):
    events = normalize_events(events_raw)

    n = 4
    names = settings.players
    seat_players = settings.seat_players

    cum = [0] * n
    rows: List[Dict[str, Any]] = []

    rw, ds, dr, d_acc = 0, 0, 0, 0
    debug_steps: List[str] = []

    stats = {pid: {"自摸": 0, "胡": 0, "放槍": 0, "詐胡": 0, "詐摸": 0} for pid in range(n)}

    def advance_dealer():
        nonlocal rw, ds, dr
        ds = (ds + 1) % 4
        dr = 0
        if ds == 0:
            rw += 1

    for idx, ev in enumerate(events, start=1):
        delta = [0] * n
        label = ""
        desc = ""

        if rw >= 4:
            ev_type = ev.get("_type", "unknown")
            label = "⚠️ 已結束"
            desc = f"忽略事件：本將已結束 (type={ev_type})"
            debug_steps.append(f"[ignored] idx={idx} rw={rw} ds={ds} dr={dr} type={ev_type}")

            row = {"#": idx, "類型": label, "說明": desc}
            for p in range(n):
                row[names[p]] = cum[p]
            rows.append(row)
            continue

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
                    desc = f"{names[off]} 詐摸[閒]：賠莊${pay_dealer}，賠閒各${amt}"
                    dealer_paid = False

            if dealer_paid:
                advance_dealer()
            else:
                dr += 1
        else:
            label = "未知"
            desc = "不支援事件"

        for p in range(n):
            cum[p] += delta[p]

        row = {"#": idx, "類型": label, "說明": desc}
        for p in range(n):
            row[names[p]] = cum[p]
        rows.append(row)

        if rw < 4:
            debug_dealer = names[seat_players[ds]]
        else:
            debug_dealer = "N/A"

        debug_steps.append(f"[#{idx}] ds={ds} dealer={debug_dealer} dr={dr} rw={rw} delta={delta} cum={cum}")

    ledger_df = pd.DataFrame(rows)
    sum_df = pd.DataFrame([{"玩家": names[i], "總分": cum[i]} for i in range(n)])

    stats_rows = []
    for pid in range(n):
        r = {"玩家": names[pid]}
        r.update(stats[pid])
        stats_rows.append(r)
    stats_df = pd.DataFrame(stats_rows)

    return ledger_df, sum_df, stats_df, rw, ds, dr, d_acc, debug_steps

