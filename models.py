from dataclasses import dataclass, field
from typing import List


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

    # 確保莊家權重開關能正確序列化
    auto_dealer_bonus: bool = True

