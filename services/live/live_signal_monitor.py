"""
即時價格串流與策略進出場訊號推播監控服務 (services/live/live_signal_monitor.py)

負責連接最新即時價格（永豐 Shioaji API 即時報價 / SQLite 最新秒 K），
驅動「布林_ATR基準 (基準 A：130% 整戶維持率控管)」即時狀態機，
並在以下事件發生時自動透過 TelegramBotService (@TMFR1_bot) 推播通知：
1. 系統與歷史回補最新更新成功資訊 (Startup / Daily Update Report)
2. 第一根突破棒預警訊號 (BAR1_BREAKOUT：收盤突破布林上軌或跌破下軌)
3. 第二根拉回確認進場訊號 (ENTRY：BUY_OPEN 做多進場 / SELL_OPEN 放空進場)
4. 持倉平倉出場訊號 (EXIT：STOP_LOSS 盤中觸價停損 / ATR_TAKE_PROFIT 動態停利 / SETTLEMENT 結算)
5. 持續監聽 Telegram Bot 互動指令 (/start, /status, /signal, /backtest, /help)。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import time
import sqlite3
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List

import pandas as pd
from dotenv import load_dotenv

from services.storage import KlineService
from services.live.bar_aggregator import BarData
from services.live.engine import LiveTradingEngine
from services.strategy.models import StrategyConfig, PositionSide
from services.strategy.bollinger_pullback import BollingerPullbackStrategy
from services.notification.telegram_bot import TelegramBotService

logger = logging.getLogger("LiveSignalMonitor")


class LiveSignalMonitor:
    """
    整合即時價格監控、布林_ATR基準策略狀態機與 Telegram Bot 即時推播之核心服務。
    """

    def __init__(
        self,
        db_path: str = "data/market_data.db",
        telegram_service: Optional[TelegramBotService] = None,
        on_ws_broadcast: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        """
        初始化即時價格與策略訊號監控器。

        參數:
            db_path (str): SQLite 資料庫路徑。
            telegram_service (Optional[TelegramBotService]): Telegram 推播服務實例。
            on_ws_broadcast (Optional[Callable[[str, Dict[str, Any]], None]]): 本地 WebSocket 廣播回呼。

        返回:
            None
        """
        load_dotenv(override=False)
        self.db_path: str = os.path.abspath(db_path)
        self.bot: TelegramBotService = telegram_service or TelegramBotService(db_path=self.db_path)
        self.on_ws_broadcast: Optional[Callable[[str, Dict[str, Any]], None]] = on_ws_broadcast

        # 採用布林_ATR基準最佳化/基準配置 (BB 30, 1.80, Pullback 0.40, ATR 14, 基準 A 130% 維持率)
        self.config: StrategyConfig = StrategyConfig(
            strategy_name="布林_ATR基準",
            symbol="TMFR1",
            interval="5m",
            bb_length=30,
            bb_std=1.80,
            atr_length=14,
            atr_tp_multiplier=2.0,
            pullback_ratio=0.40,
            initial_capital=105150.0,
            max_leverage=8.0,
            margin_req=35050.0,
            point_value=10.0,
            fee_per_contract=18.0,
            tax_rate=0.00002,
            slippage_points=0.0,
        )
        self.strategy: BollingerPullbackStrategy = BollingerPullbackStrategy(config=self.config)
        self.engine: LiveTradingEngine = LiveTradingEngine(
            strategy=self.strategy,
            on_event=self._handle_engine_event,
        )

        self.latest_price: float = 0.0
        self.latest_time: str = "--"
        self._last_db_time: str = ""
        self._last_notified_bar1_ts: int = 0
        self._notifications_enabled: bool = False

        # 註冊狀態提供者供 Telegram /status 指令即時讀取
        self.bot.live_status_provider = self.get_live_snapshot

    def warmup_from_sqlite(self, warmup_bars: int = 300) -> Dict[str, Any]:
        """
        從 SQLite 載入最近 N 根 5 分 K 線進行策略與指標暖機 (Warmup)。
        暖機期間不發送歷史舊訊號，暖機完成後開啟即時訊號推播。

        參數:
            warmup_bars (int): 用於暖機之最近 5 分 K 根數，預設 300 根。

        返回:
            Dict[str, Any]: 暖機後之引擎狀態與最新價格摘要。
        """
        self._notifications_enabled = False
        try:
            svc = KlineService(db_path=self.db_path, include_history=True, since="2026-09-25")
            bars_5m = svc.get_kbars("5m", limit=warmup_bars)
            for b in bars_5m[:-1]:
                bar_obj = BarData(
                    timestamp=int(b["time"]),
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=int(b["volume"]),
                    is_closed=True,
                )
                self.engine._handle_bar_close(bar_obj)

            if bars_5m:
                last_b = bars_5m[-1]
                self.latest_price = float(last_b["close"])
                self.latest_time = str(last_b["datetime"])

            # 記錄資料庫最新一筆秒 K 時間戳
            if os.path.exists(self.db_path):
                conn = sqlite3.connect(self.db_path)
                row = conn.execute("SELECT MAX(time), close FROM tmf10_second_bars").fetchone()
                if row and row[0]:
                    self._last_db_time = str(row[0])
                    self.latest_time = str(row[0])
                    if row[1] is not None:
                        self.latest_price = float(row[1])
                conn.close()
        except Exception as e:
            logger.warning(f"策略暖機過程發生警告: {e}")
        finally:
            self._notifications_enabled = True

        return self.get_live_snapshot()

    def _handle_engine_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        接收 LiveTradingEngine 派發之內部事件，轉發至本地 WebSocket 並於進出場訊號產生時推播 Telegram。

        參數:
            event_type (str): 事件類別 ('position_open', 'trade_closed', 'bar_close', 'bar_update', 'tick')。
            data (Dict[str, Any]): 事件負載資料。

        返回:
            None
        """
        if self.on_ws_broadcast:
            try:
                self.on_ws_broadcast(event_type, data)
            except Exception:
                pass

        if not self._notifications_enabled:
            return

        if event_type == "position_open":
            self.bot.notify_strategy_signal({
                "event_type": "ENTRY",
                "side": data.get("side", "NONE"),
                "price": data.get("entry_price", self.latest_price),
                "time_str": data.get("entry_time", self.latest_time),
                "qty": data.get("qty", 1),
                "stop_loss_price": data.get("stop_loss_price", 0.0),
                "equity": self.engine.equity,
                "reason": data.get("reason", "布林突破拉回確認進場"),
            })
        elif event_type == "trade_closed":
            self.bot.notify_strategy_signal({
                "event_type": "EXIT",
                "side": data.get("side", "NONE"),
                "entry_price": data.get("entry_price", 0.0),
                "price": data.get("exit_price", self.latest_price),
                "time_str": data.get("exit_time", self.latest_time),
                "qty": data.get("qty", 1),
                "exit_reason": data.get("exit_reason", "策略平倉"),
                "net_pnl": data.get("net_pnl", 0.0),
                "equity_after": data.get("equity_after", self.engine.equity),
            })
        elif event_type == "bar_close":
            # 檢查是否剛觸發 Bar 1 突破預警
            if (
                self.strategy.pending_signal_side != PositionSide.NONE
                and self.strategy.signal_bar_timestamp > self._last_notified_bar1_ts
            ):
                self._last_notified_bar1_ts = self.strategy.signal_bar_timestamp
                self.bot.notify_strategy_signal({
                    "event_type": "BAR1_BREAKOUT (第一根突破訊號棒成立)",
                    "side": self.strategy.pending_signal_side.value,
                    "price": self.strategy.signal_bar_close,
                    "time_str": datetime.fromtimestamp(self.strategy.signal_bar_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                    "signal_bar_q1": self.strategy.signal_bar_q1,
                    "stop_loss_price": self.strategy.signal_bar_extreme,
                    "equity": self.engine.equity,
                    "reason": "5 分 K 收盤突破布林通道，進入第二根拉回確認觀察期",
                })

    def poll_sqlite_new_bars(self) -> int:
        """
        輪詢 SQLite 資料庫是否有新寫入之秒 K 線，若有則依序送入即時引擎觸發 K 棒更新與策略訊號。

        返回:
            int: 本次新增處理之秒 K 筆數。
        """
        if not os.path.exists(self.db_path):
            return 0
        try:
            conn = sqlite3.connect(self.db_path)
            if self._last_db_time:
                rows = conn.execute(
                    "SELECT time, close, volume FROM tmf10_second_bars WHERE time > ? ORDER BY time ASC LIMIT 5000",
                    (self._last_db_time,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT time, close, volume FROM tmf10_second_bars ORDER BY time DESC LIMIT 1"
                ).fetchall()
            conn.close()
        except Exception:
            return 0

        if not rows:
            return 0

        count = 0
        for t_str, close_val, vol_val in rows:
            self._last_db_time = str(t_str)
            self.latest_time = str(t_str)
            self.latest_price = float(close_val)
            ts = int(pd.Timestamp(t_str).timestamp())
            self.engine.on_tick(timestamp=ts, price=float(close_val), volume=int(vol_val or 1))
            count += 1
        return count

    def poll_shioaji_snapshot_once(self, api: Any, contract: Any) -> Optional[Dict[str, Any]]:
        """
        透過已登入之 Shioaji 實例擷取微台指 (TMFR1) 最新即時 Snapshot 報價並餵入策略引擎。

        參數:
            api (Any): Shioaji 實例。
            contract (Any): 微台指近月合約物件。

        返回:
            Optional[Dict[str, Any]]: 若取得有效新報價則回傳包含 timestamp, price, volume 之字典。
        """
        try:
            snaps = api.snapshots([contract])
            if not snaps:
                return None
            s = snaps[0]
            price = float(getattr(s, "close", 0.0) or 0.0)
            vol = int(getattr(s, "volume", 1) or 1)
            ts_ns = int(getattr(s, "ts", 0) or 0)
            if price <= 0:
                return None
            if ts_ns > 0:
                # Shioaji snapshot.ts 為台灣時間奈秒時間戳，轉為本地時間字串再解析為 Epoch 秒
                dt = pd.Timestamp(ts_ns).tz_localize(None)
                t_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                epoch_sec = int(dt.timestamp())
            else:
                now = datetime.now()
                t_str = now.strftime("%Y-%m-%d %H:%M:%S")
                epoch_sec = int(now.timestamp())

            if t_str > self.latest_time or abs(price - self.latest_price) >= 1.0:
                self.latest_price = price
                self.latest_time = t_str
                self.engine.on_tick(timestamp=epoch_sec, price=price, volume=vol)
                return {"timestamp": epoch_sec, "datetime": t_str, "price": price, "volume": vol}
        except Exception as e:
            logger.debug(f"Shioaji snapshot 輪詢略過: {e}")
        return None

    def get_live_snapshot(self) -> Dict[str, Any]:
        """
        取得目前即時報價與策略持倉狀態快照。

        返回:
            Dict[str, Any]: 即時狀態字典。
        """
        st = self.engine.get_status()
        pos = st.get("position", {})
        return {
            "latest_price": self.latest_price,
            "latest_time": self.latest_time,
            "current_side": pos.get("side", "NONE"),
            "qty": pos.get("qty", 0),
            "entry_price": pos.get("entry_price", 0.0),
            "stop_loss_price": pos.get("stop_loss_price", 0.0),
            "equity": st.get("equity", self.engine.equity),
            "realized_pnl": st.get("realized_pnl", self.engine.realized_pnl),
        }
