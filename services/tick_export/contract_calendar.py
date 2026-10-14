"""
微台10 近月合約換月日曆 (services/tick_export/contract_calendar.py)

集中定義「某個時間點屬於哪一個合約月份」的單一規則，供每日同步 (標記實體合約代碼)
與月份分頁 Excel 匯出器 (以合約月份切分工作表) 共用，避免兩處各自實作而產生不一致。

換月規則 (已以 Shioaji TMFR1 實際 Tick 驗證)：
1. 每個月合約之最後交易日為該月第三個星期三，當日日盤收於 13:30 (一般日為 13:45)。
2. 到期日 15:00 起之夜盤 (屬次一交易日) 已是次月合約。
3. 因此「時間 t 所屬合約月份」= 第一個滿足 t < (該月到期日 15:00) 的月份。

已知限制：到期日遇國定假日或颱風停市時，期交所會順延；此情形請登錄於 EXPIRY_OVERRIDES。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import re
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Dict, Optional, Tuple, Union

MONTH_CODES: str = "ABCDEFGHIJKL"  # 期交所月份代碼：A=1 月 ... L=12 月
CONTRACT_PREFIX: str = "TMF"
ROLL_TIME: time = time(15, 0)  # 到期日 15:00 起即為次月合約
TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"
_CODE_PATTERN = re.compile(r"^TMF([A-L])(\d)$")

# 例外到期日表：{(年, 月): 實際最後交易日}。預設為空，僅在停市順延時才需登錄。
EXPIRY_OVERRIDES: Dict[Tuple[int, int], date] = {}


def third_wednesday(year: int, month: int) -> date:
    """
    計算指定年月之第三個星期三。

    參數:
        year (int): 西元年。
        month (int): 月份 (1~12)。

    返回:
        date: 該月第三個星期三之日期。
    """
    first_day = date(year, month, 1)
    offset_to_first_wed = (2 - first_day.weekday()) % 7  # weekday(): 週三 = 2
    return first_day + timedelta(days=offset_to_first_wed + 14)


def expiry_date(year: int, month: int) -> date:
    """
    取得指定合約月份之最後交易日 (優先採用 EXPIRY_OVERRIDES，否則為第三個星期三)。

    參數:
        year (int): 合約年份。
        month (int): 合約月份 (1~12)。

    返回:
        date: 最後交易日。
    """
    return EXPIRY_OVERRIDES.get((year, month)) or third_wednesday(year, month)


@lru_cache(maxsize=256)
def roll_cutoff(year: int, month: int) -> datetime:
    """
    計算指定合約月份資料之時間上界 (不含)：最後交易日 15:00。

    參數:
        year (int): 合約年份。
        month (int): 合約月份 (1~12)。

    返回:
        datetime: 時間 < 此值之資料屬於該合約月份 (自前一合約之上界起算)。
    """
    return datetime.combine(expiry_date(year, month), ROLL_TIME)


def _to_datetime(ts: Union[str, datetime]) -> datetime:
    """
    將字串或 datetime 統一轉為 datetime。

    參數:
        ts (Union[str, datetime]): 格式 YYYY-MM-DD HH:MM:SS 之字串或 datetime 物件。

    返回:
        datetime: 對應之 datetime 物件。
    """
    if isinstance(ts, datetime):
        return ts
    return datetime.strptime(str(ts)[:19], TIME_FORMAT)


def next_month(year: int, month: int) -> Tuple[int, int]:
    """
    取得下一個月份。

    參數:
        year (int): 西元年。
        month (int): 月份 (1~12)。

    返回:
        Tuple[int, int]: (年, 月) 之下一月份。
    """
    return (year + 1, 1) if month == 12 else (year, month + 1)


def prev_month(year: int, month: int) -> Tuple[int, int]:
    """
    取得上一個月份。

    參數:
        year (int): 西元年。
        month (int): 月份 (1~12)。

    返回:
        Tuple[int, int]: (年, 月) 之上一月份。
    """
    return (year - 1, 12) if month == 1 else (year, month - 1)


def contract_month_for(ts: Union[str, datetime]) -> Tuple[int, int]:
    """
    判定某時間點所屬之近月合約月份。

    參數:
        ts (Union[str, datetime]): 秒 K 時間戳 (字串格式 YYYY-MM-DD HH:MM:SS 或 datetime)。

    返回:
        Tuple[int, int]: (合約年, 合約月)。
    """
    moment = _to_datetime(ts)
    if moment >= roll_cutoff(moment.year, moment.month):
        return next_month(moment.year, moment.month)
    return (moment.year, moment.month)


def contract_code_for_month(year: int, month: int) -> str:
    """
    由合約年月組出期交所商品代碼 (例如 2026-10 → TMFJ6)。

    參數:
        year (int): 合約年份。
        month (int): 合約月份 (1~12)。

    返回:
        str: 商品代碼，格式 TMF + 月份代碼 + 西元年個位數。
    """
    return f"{CONTRACT_PREFIX}{MONTH_CODES[month - 1]}{year % 10}"


def contract_code_for(ts: Union[str, datetime]) -> str:
    """
    判定某時間點所屬之近月合約商品代碼。

    參數:
        ts (Union[str, datetime]): 秒 K 時間戳。

    返回:
        str: 商品代碼 (例如 2026-10-14 10:00:00 → TMFJ6)。
    """
    year, month = contract_month_for(ts)
    return contract_code_for_month(year, month)


def parse_contract_code(code: str) -> Optional[Tuple[int, int]]:
    """
    將實體合約代碼解析為 (年, 月)；非實體合約代碼 (如連續合約 TMFR1) 回傳 None。

    年份僅有個位數，微台10 於 2024 年上市，故 4~9 對應 2024~2029，0~3 對應 2030~2033。

    參數:
        code (str): 商品代碼，例如 TMFJ6。

    返回:
        Optional[Tuple[int, int]]: (合約年, 合約月)；無法解析時為 None。
    """
    matched = _CODE_PATTERN.match(code or "")
    if not matched:
        return None
    month = MONTH_CODES.index(matched.group(1)) + 1
    digit = int(matched.group(2))
    year = 2020 + digit if digit >= 4 else 2030 + digit
    return (year, month)


def contract_key(year: int, month: int) -> str:
    """
    產生合約月份鍵 (例如 2026-10)，用於工作表命名與分組。

    參數:
        year (int): 合約年份。
        month (int): 合約月份。

    返回:
        str: 格式 YYYY-MM 之字串。
    """
    return f"{year:04d}-{month:02d}"


def contract_time_bounds(year: int, month: int) -> Tuple[str, str]:
    """
    取得某合約月份依換月規則推算之時間區間 (左閉右開)，供連續合約資料分組使用。

    參數:
        year (int): 合約年份。
        month (int): 合約月份。

    返回:
        Tuple[str, str]: (下界, 上界)，格式 YYYY-MM-DD HH:MM:SS；
            下界 = 前一合約最後交易日 15:00，上界 = 本合約最後交易日 15:00。
    """
    prev_year, prev_mon = prev_month(year, month)
    lower = roll_cutoff(prev_year, prev_mon)
    upper = roll_cutoff(year, month)
    return lower.strftime(TIME_FORMAT), upper.strftime(TIME_FORMAT)
