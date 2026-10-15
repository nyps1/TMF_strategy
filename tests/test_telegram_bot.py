"""
Telegram Bot 推播、持久化佇列與即時策略訊號監控單元測試 (tests/test_telegram_bot.py)

驗證：
1. 無已綁定 Chat ID 時，notify_update_success 與 notify_strategy_signal 自動寫入持久化待發送佇列。
2. 新訂閱者註冊並觸發 flush_pending_messages 時，佇列內訊息全數補發並清空佇列。
3. LiveSignalMonitor 於策略進出場事件發生時，自動觸發 TelegramBotService.notify_strategy_signal。
"""

import os
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import patch

from services.notification.telegram_bot import TelegramBotService
from services.live.live_signal_monitor import LiveSignalMonitor


class TestTelegramBotService(unittest.TestCase):
    """驗證 TelegramBotService 與 LiveSignalMonitor 核心行為。"""

    def setUp(self) -> None:
        """建立隔離之暫存目錄與測試實例。"""
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.sub_path = os.path.join(base, "subs.json")
        self.pend_path = os.path.join(base, "pending.json")
        self.db_path = os.path.join(base, "empty.db")
        self.bot = TelegramBotService(
            token="123456:TEST_TOKEN_STRING_FOR_UNIT_TEST",
            subscribers_path=self.sub_path,
            pending_path=self.pend_path,
            db_path=self.db_path,
        )

    def tearDown(self) -> None:
        """清理暫存目錄。"""
        self.tmp.cleanup()

    def test_queue_when_no_subscribers_and_flush_on_register(self) -> None:
        """尚無訂閱者時應存入佇列；綁定 Chat ID 後 flush 應立即補發並清空佇列。"""
        sent_messages: List[tuple] = []

        def fake_send(chat_id: int, text: str) -> Dict[str, Any]:
            sent_messages.append((chat_id, text))
            return {"ok": True}

        with patch.object(self.bot, "sync_subscribers_and_process_commands", return_value={"ok": True}), \
             patch.object(self.bot, "_send_to_chat", side_effect=fake_send):
            res1 = self.bot.notify_update_success(report={
                "stop_reason": "QUOTA_PAUSED",
                "inserted_rows": 314701,
                "remaining_mb": 41.3,
                "reserve_mb": 40.0,
            })
            self.assertEqual(res1["status"], "QUEUED")

            res2 = self.bot.notify_strategy_signal({
                "event_type": "ENTRY",
                "side": "LONG",
                "price": 45800.0,
                "time_str": "2026-10-15 09:45:00",
                "qty": 2,
                "stop_loss_price": 45740.0,
                "equity": 105150.0,
                "reason": "三根 K 棒突破拉回確認成立",
            })
            self.assertEqual(res2["status"], "QUEUED")
            self.assertEqual(len(sent_messages), 0)

            # 模擬使用者於 Telegram 點擊 /start 綁定 Chat ID 99887766
            is_new = self.bot.register_chat_id(99887766, meta={"username": "trader_user"})
            self.assertTrue(is_new)

            flushed = self.bot.flush_pending_messages(chat_id=99887766)
            self.assertEqual(flushed, 2)
            self.assertEqual(len(sent_messages), 2)
            self.assertIn("最近更新成功通知", sent_messages[0][1])
            self.assertIn("策略進場訊號通知", sent_messages[1][1])

            # 綁定後再次發送訊號應直接 SENT
            res3 = self.bot.notify_strategy_signal({
                "event_type": "EXIT",
                "side": "LONG",
                "entry_price": 45800.0,
                "price": 45880.0,
                "time_str": "2026-10-15 10:15:00",
                "qty": 2,
                "exit_reason": "ATR 動態停利",
                "net_pnl": 1500.0,
                "equity_after": 106650.0,
            })
            self.assertEqual(res3["status"], "SENT")
            self.assertEqual(len(sent_messages), 3)
            self.assertIn("策略出場訊號通知", sent_messages[2][1])

    def test_live_signal_monitor_emits_to_telegram(self) -> None:
        """驗證 LiveSignalMonitor 在進出場事件時觸發 TelegramBotService.notify_strategy_signal。"""
        captured_signals: List[Dict[str, Any]] = []
        monitor = LiveSignalMonitor(db_path=self.db_path, telegram_service=self.bot)
        monitor._notifications_enabled = True

        with patch.object(self.bot, "notify_strategy_signal", side_effect=lambda p: captured_signals.append(p)):
            monitor._handle_engine_event("position_open", {
                "side": "SHORT",
                "qty": 2,
                "entry_price": 45600.0,
                "entry_time": "2026-10-15 09:50:00",
                "stop_loss_price": 45660.0,
                "reason": "反彈確認成立放空",
            })
            monitor._handle_engine_event("trade_closed", {
                "side": "SHORT",
                "qty": 2,
                "entry_price": 45600.0,
                "exit_price": 45520.0,
                "exit_time": "2026-10-15 10:20:00",
                "exit_reason": "ATR 停利",
                "net_pnl": 1510.0,
                "equity_after": 106660.0,
            })

        self.assertEqual(len(captured_signals), 2)
        self.assertEqual(captured_signals[0]["event_type"], "ENTRY")
        self.assertEqual(captured_signals[0]["side"], "SHORT")
        self.assertEqual(captured_signals[1]["event_type"], "EXIT")
        self.assertEqual(captured_signals[1]["net_pnl"], 1510.0)


if __name__ == "__main__":
    unittest.main()
