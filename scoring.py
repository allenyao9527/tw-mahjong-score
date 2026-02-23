from typing import Any
from models import Settings


def amount_A(settings: Settings, tai: int) -> int:
    try:
        base = int(getattr(settings, "base", 0))
        t = int(tai)
        tv = int(getattr(settings, "tai_value", 0))
    except Exception:
        base, t, tv = 0, 0, 0
    return base + t * tv


def dealer_bonus_tai(dealer_run: int) -> int:
    """
    上莊=1台, 連1=3台, 連2=5台, 連3=7台
    => bonus = 1 + 2*dealer_run
    """
    return 1 + 2 * int(dealer_run)

