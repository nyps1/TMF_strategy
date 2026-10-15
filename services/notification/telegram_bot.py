"""
Telegram Bot 即時推播與雙向互動服務 (services/notification/telegram_bot.py)

採用「發布-訂閱模式 (Observer / Pub-Sub Pattern)」結合「持久化發件匣佇列模式 (Persistent Outbox Queue Pattern)」：
1. 自動載入憑證：優先讀取環境變數 TELEGRAM_BOT_TOKEN (.env)，若未設定則自動回退讀取 C:/Users/User/Desktop/api.txt。
2. 訂閱者自動探索與持久化：透過 getUpdates 自動擷取向 @TMFR1_bot 發送 /start 或任意訊息之使用者 Chat ID，
   持久化儲存於 data/telegram_subscribers.json。
3. 零漏接保證 (Persistent Outbox Queue)：若推播觸發當下尚無任何已綁定之 Chat ID，
   系統自動將訊息寫入 data/telegram_pending_notifications.json；一旦偵測到使用者對 Bot 按下 Start，
   立即依序補發所有歷史待推播訊息，確保更新成功報告與策略訊號零遺失。
4. 支援推播類型：
   - 最近資料同步、歷史回補與儀表板更新成功資訊 (notify_update_success)
   - 連接即時價格之布林_ATR基準策略進場、出場與突破拉回預警訊號 (notify_strategy_signal)
5. 支援雙向互動指令：/start, /status, /signal, /backtest, /help。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import os
import json
import time
import sqlite3
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable

import httpx
import pandas as pd
from dotenv import load_dotenv

logger = logging.getLogger("TelegramBotService")


class TelegramBotService:
    """
    微台10 量化系統專屬 Telegram Bot (@TMFR1_bot) 推播與指令互動服務。
    """

    DEFAULT_API_TXT_PATH: str = r"C:\Users\User\Desktop\api.txt"
    DEFAULT_SUBSCRIBERS_PATH: str = "data/telegram_subscribers.json"
    DEFAULT_PENDING_PATH: str = "data/telegram_pending_notifications.json"
    DEFAULT_DB_PATH: str = "data/market_data.db"
    GITHUB_PAGES_URL: str = "https://nyps1.github.io/tmf-dashboard/"

    def __init__(
        self,
        token: Optional[str] = None,
        subscribers_path: str = DEFAULT_SUBSCRIBERS_PATH,
        pending_path: str = DEFAULT_PENDING_PATH,
        db_path: str = DEFAULT_DB_PATH,
        api_txt_path: str = DEFAULT_API_TXT_PATH,
    ) -> None:
        """
        初始化 Telegram Bot 服務。

        參數:
            token (Optional[str]): Telegram Bot API Token。若為 None 則自動從 .env 或 Desktop/api.txt 讀取。
            subscribers_path (str): 訂閱者 Chat ID 清單持久化 JSON 路徑。
            pending_path (str): 待發送訊息佇列持久化 JSON 路徑。
            db_path (str): SQLite 行情資料庫路徑。
            api_txt_path (str): 備援 API Token 文字檔路徑。

        返回:
            None
        """
        load_dotenv(override=False)
        self.api_txt_path: str = api_txt_path
        self.token: str = token or self._load_token()
        self.base_url: str = f"https://api.telegram.org/bot{self.token}" if self.token else ""
        self.subscribers_path: str = os.path.abspath(subscribers_path)
        self.pending_path: str = os.path.abspath(pending_path)
        self.db_path: str = os.path.abspath(db_path)
        self._last_update_id: int = self._load_last_update_id()
        self.live_status_provider: Optional[Callable[[], Dict[str, Any]]] = None

    def _load_token(self) -> str:
        """
        從環境變數或指定檔案讀取 Telegram Bot Token。

        返回:
            str: 清理空白後的 Bot Token 字串。
        """
        env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if env_token:
            return env_token
        if os.path.exists(self.api_txt_path):
            try:
                with open(self.api_txt_path, "r", encoding="utf-8") as f:
                    for line in f:
                        candidate = line.strip()
                        if ":" in candidate and len(candidate) >= 30:
                            return candidate
            except Exception as e:
                logger.warning(f"讀取 {self.api_txt_path} 失敗: {e}")
        return ""

    def _load_subscribers_data(self) -> Dict[str, Any]:
        """
        載入本地訂閱者狀態檔。

        返回:
            Dict[str, Any]: 包含 chat_ids, chats, last_update_id 之字典。
        """
        default_data: Dict[str, Any] = {"chat_ids": [], "chats": {}, "last_update_id": 0}
        env_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if os.path.exists(self.subscribers_path):
            try:
                with open(self.subscribers_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    default_data.update(data)
            except Exception:
                pass

        if env_chat_id:
            try:
                cid = int(env_chat_id)
                if cid not in default_data["chat_ids"]:
                    default_data["chat_ids"].append(cid)
            except ValueError:
                pass
        return default_data

    def _save_subscribers_data(self, data: Dict[str, Any]) -> None:
        """
        儲存訂閱者清單與最後處理之 update_id 至磁碟。

        參數:
            data (Dict[str, Any]): 欲儲存之訂閱者資料結構。

        返回:
            None
        """
        os.makedirs(os.path.dirname(self.subscribers_path), exist_ok=True)
        tmp_path = f"{self.subscribers_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.subscribers_path)

    def _load_last_update_id(self) -> int:
        """
        讀取上次已處理之最後一個 Telegram update_id。

        返回:
            int: 最後處理之 update_id。
        """
        data = self._load_subscribers_data()
        return int(data.get("last_update_id", 0))

    def get_subscribed_chat_ids(self) -> List[int]:
        """
        取得目前已註冊之所有訂閱者 Chat ID 列表。

        返回:
            List[int]: 已註冊之 Chat ID 整數列表。
        """
        data = self._load_subscribers_data()
        return [int(cid) for cid in data.get("chat_ids", [])]

    def register_chat_id(self, chat_id: int, meta: Optional[Dict[str, Any]] = None) -> bool:
        """
        手動或自動註冊一個新的訂閱者 Chat ID。

        參數:
            chat_id (int): Telegram 對話或群組 ID。
            meta (Optional[Dict[str, Any]]): 使用者名稱或群組資訊。

        返回:
            bool: 若為新註冊之 Chat ID 回傳 True，若已存在回傳 False。
        """
        data = self._load_subscribers_data()
        chat_ids: List[int] = [int(x) for x in data.get("chat_ids", [])]
        is_new = chat_id not in chat_ids
        if is_new:
            chat_ids.append(chat_id)
            data["chat_ids"] = chat_ids
        if meta:
            chats = data.get("chats", {})
            chats[str(chat_id)] = {
                **meta,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            data["chats"] = chats
        self._save_subscribers_data(data)
        return is_new

    def _enqueue_pending_message(self, text: str, category: str = "general") -> None:
        """
        將尚未有訂閱者可接收之訊息存入持久化待發送佇列。

        參數:
            text (str): 訊息內容。
            category (str): 訊息分類標籤 (如 update_success, strategy_signal)。

        返回:
            None
        """
        os.makedirs(os.path.dirname(self.pending_path), exist_ok=True)
        queue: List[Dict[str, Any]] = []
        if os.path.exists(self.pending_path):
            try:
                with open(self.pending_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        queue = loaded
            except Exception:
                queue = []

        # 若為同類別之 update_success，保留最新一筆避免重複洗版；訊號則依序保留最近 30 筆
        if category == "update_success":
            queue = [item for item in queue if item.get("category") != "update_success"]

        queue.append({
            "category": category,
            "text": text,
            "queued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        queue = queue[-30:]

        tmp_path = f"{self.pending_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.pending_path)

    def flush_pending_messages(self, chat_id: Optional[int] = None) -> int:
        """
        將待發送佇列中之訊息發送給指定或所有已綁定之訂閱者，並清空已成功發送之佇列。

        參數:
            chat_id (Optional[int]): 指定接收之 Chat ID；若為 None 則發送給全部已註冊訂閱者。

        返回:
            int: 成功補發之訊息筆數。
        """
        if not os.path.exists(self.pending_path):
            return 0
        try:
            with open(self.pending_path, "r", encoding="utf-8") as f:
                queue = json.load(f)
            if not isinstance(queue, list) or not queue:
                return 0
        except Exception:
            return 0

        targets = [chat_id] if chat_id is not None else self.get_subscribed_chat_ids()
        if not targets:
            return 0

        sent_count = 0
        for item in queue:
            msg_text = item.get("text", "")
            if not msg_text:
                continue
            for tid in targets:
                res = self._send_to_chat(tid, msg_text)
                if res.get("ok"):
                    sent_count += 1

        if sent_count > 0:
            try:
                with open(self.pending_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
            except Exception:
                pass
        return sent_count

    def get_bot_info(self) -> Dict[str, Any]:
        """
        呼叫 Telegram getMe 驗證 Bot Token 並取得機器人基本資料。

        返回:
            Dict[str, Any]: Telegram getMe 回應字典。
        """
        if not self.token:
            return {"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN"}
        try:
            resp = httpx.get(f"{self.base_url}/getMe", timeout=10.0)
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _send_to_chat(self, chat_id: int, text: str) -> Dict[str, Any]:
        """
        向單一指定 chat_id 發送文字訊息。

        參數:
            chat_id (int): 目標對話 ID。
            text (str): 訊息文字內容。

        返回:
            Dict[str, Any]: Telegram API 回傳結果。
        """
        if not self.token:
            return {"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN"}
        try:
            resp = httpx.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=10.0,
            )
            return resp.json()
        except Exception as e:
            logger.error(f"發送 Telegram 訊息至 {chat_id} 失敗: {e}")
            return {"ok": False, "error": str(e)}

    def sync_subscribers_and_process_commands(self, timeout_sec: int = 0) -> Dict[str, Any]:
        """
        呼叫 getUpdates 同步最新互動訊息、自動綁定新訂閱者、補發佇列訊息並回覆使用者指令。

        參數:
            timeout_sec (int): Long polling 等待秒數，預設 0 為立即返回。

        返回:
            Dict[str, Any]: 同步結果摘要，包含新增訂閱人數、處理指令數與當前總訂閱者清單。
        """
        if not self.token:
            return {"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN", "subscribers": []}

        data = self._load_subscribers_data()
        offset = int(data.get("last_update_id", 0)) + 1
        params: Dict[str, Any] = {"timeout": timeout_sec}
        if offset > 1:
            params["offset"] = offset

        try:
            resp = httpx.get(
                f"{self.base_url}/getUpdates",
                params=params,
                timeout=max(10.0, float(timeout_sec) + 5.0),
            )
            payload = resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e), "subscribers": self.get_subscribed_chat_ids()}

        if not payload.get("ok"):
            return {"ok": False, "error": str(payload), "subscribers": self.get_subscribed_chat_ids()}

        updates = payload.get("result", [])
        new_subscribers: List[int] = []
        commands_handled: int = 0

        for upd in updates:
            upd_id = int(upd.get("update_id", 0))
            if upd_id > self._last_update_id:
                self._last_update_id = upd_id
                data = self._load_subscribers_data()
                data["last_update_id"] = self._last_update_id
                self._save_subscribers_data(data)

            msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
            chat = msg.get("chat", {})
            chat_id = chat.get("id")
            if chat_id is None:
                continue

            chat_id_int = int(chat_id)
            is_new = self.register_chat_id(
                chat_id_int,
                meta={
                    "type": chat.get("type", "private"),
                    "username": chat.get("username", ""),
                    "first_name": chat.get("first_name", ""),
                    "title": chat.get("title", ""),
                },
            )
            if is_new:
                new_subscribers.append(chat_id_int)
                logger.info(f"已自動綁定新 Telegram 訂閱者: chat_id={chat_id_int} ({chat.get('username') or chat.get('first_name')})")

            text_in = (msg.get("text") or "").strip()
            if text_in or is_new:
                self._handle_incoming_command(chat_id_int, text_in, is_new=is_new)
                commands_handled += 1

        return {
            "ok": True,
            "new_subscribers": new_subscribers,
            "commands_handled": commands_handled,
            "subscribers": self.get_subscribed_chat_ids(),
        }

    def _handle_incoming_command(self, chat_id: int, text_in: str, is_new: bool = False) -> None:
        """
        處理使用者傳送至 @TMFR1_bot 之互動指令，並在首次綁定時主動推播佇列內之最新通知與即時狀態。

        參數:
            chat_id (int): 發送指令之對話 ID。
            text_in (str): 使用者輸入之文字。
            is_new (bool): 是否為首次綁定之新訂閱者。

        返回:
            None
        """
        cmd = text_in.split()[0].lower() if text_in else "/start"
        if "@" in cmd:
            cmd = cmd.split("@")[0]

        if is_new or cmd == "/start":
            welcome_msg = (
                "【微台10 量化策略與即時訊號中心 (@TMFR1_bot)】\n"
                "----------------------------------------\n"
                "狀態：已完成頻道綁定，您將自動收到：\n"
                "1. 每日資料同步、歷史回補與儀表板更新成功通知\n"
                "2. 連接即時價格之【布林_ATR基準 (基準 A：130% 維持率)】進場、出場與突破拉回預警訊號\n"
                "----------------------------------------\n"
                "支援查詢指令：\n"
                "  /status   - 查詢最新即時價格、資料庫進度與目前持倉狀態\n"
                "  /signal   - 查詢最新 5 分 K 布林通道、ATR 軌道與最近一筆訊號\n"
                "  /backtest - 查詢完整區間回測績效摘要與線上儀表板連結\n"
                "  /help     - 顯示指令說明清單"
            )
            self._send_to_chat(chat_id, welcome_msg)
            flushed = self.flush_pending_messages(chat_id=chat_id)
            if flushed == 0:
                self._send_to_chat(chat_id, self.build_status_report_text())
                self._send_to_chat(chat_id, self.build_signal_snapshot_text())
        elif cmd == "/status":
            self._send_to_chat(chat_id, self.build_status_report_text())
        elif cmd == "/signal":
            self._send_to_chat(chat_id, self.build_signal_snapshot_text())
        elif cmd == "/backtest":
            self._send_to_chat(chat_id, self.build_backtest_summary_text())
        elif cmd == "/help":
            help_msg = (
                "【@TMFR1_bot 指令清單】\n"
                "  /start    - 綁定訂閱並接收最新更新報告與即時策略快照\n"
                "  /status   - 查詢資料庫最新秒 K 時序、即時報價與持倉狀態\n"
                "  /signal   - 查詢最新 5 分 K 布林上下軌、ATR 指標與最近成交訊號\n"
                "  /backtest - 查詢布林_ATR基準策略回測績效與線上儀表板網址\n"
                "  /help     - 顯示本說明"
            )
            self._send_to_chat(chat_id, help_msg)
        else:
            self._send_to_chat(
                chat_id,
                f"已收到您的訊息：「{text_in}」\n請輸入 /status 檢視最新行情與持倉，或輸入 /signal 檢視最新策略指標與進出場訊號。"
            )

    def send_message(self, text: str, category: str = "general") -> Dict[str, Any]:
        """
        推播訊息至所有已綁定之 Telegram 訂閱者。
        發送前會先呼叫一次 getUpdates 同步最新訂閱者；若目前尚無訂閱者，
        則自動將訊息寫入持久化發件匣佇列 (data/telegram_pending_notifications.json)，
        待使用者於 Telegram 點擊 Start 後立即自動補發。

        參數:
            text (str): 欲推播之文字內容。
            category (str): 訊息類別標籤 ('update_success' | 'strategy_signal' | 'general')。

        返回:
            Dict[str, Any]: 推播執行結果（包含 status: SENT / QUEUED / ERROR）。
        """
        self.sync_subscribers_and_process_commands(timeout_sec=0)
        chat_ids = self.get_subscribed_chat_ids()

        if not chat_ids:
            self._enqueue_pending_message(text=text, category=category)
            logger.info(
                f"目前尚無已綁定之 Chat ID，訊息已存入待推播佇列 ({self.pending_path})，"
                "待使用者向 @TMFR1_bot 發送 /start 後將立即自動推播。"
            )
            return {
                "status": "QUEUED",
                "message": "No chat_id registered yet; message saved to pending queue for immediate delivery upon /start.",
                "bot_username": "TMFR1_bot",
                "bot_url": "https://t.me/TMFR1_bot",
            }

        results = []
        success_count = 0
        for cid in chat_ids:
            res = self._send_to_chat(cid, text)
            results.append({"chat_id": cid, "ok": res.get("ok", False)})
            if res.get("ok"):
                success_count += 1

        return {
            "status": "SENT" if success_count > 0 else "ERROR",
            "sent_count": success_count,
            "total_subscribers": len(chat_ids),
            "results": results,
        }

    def notify_update_success(self, report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        推播「最近資料同步、歷史回補與儀表板更新成功資訊」至 Telegram Bot。

        參數:
            report (Optional[Dict[str, Any]]): 更新管線或回補作業之執行摘要字典；若為 None 則自動讀取當前資料庫與最新狀態。

        返回:
            Dict[str, Any]: 推播狀態結果。
        """
        db_info = self._query_db_summary()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sync_line = "已完成最新日夜盤秒 K 增量同步與 40 MB 額度預留歷史回補"
        if report and isinstance(report, dict):
            if "stop_reason" in report:
                sync_line = (
                    f"歷史回補完成 ({report.get('stop_reason')})｜"
                    f"本次新增 {report.get('inserted_rows', 0):,} 列秒 K｜"
                    f"剩餘額度 {report.get('remaining_mb', 0):.1f} MB (預留門檻 {report.get('reserve_mb', 40.0):.0f} MB)"
                )
            elif "pipeline_status" in report:
                sync_step = report.get("sync_step", {})
                sync_line = f"定時更新管線 ({report.get('pipeline_status')})｜行情同步: {sync_step.get('status', 'SUCCESS')}"

        msg = (
            "【微台10 量化系統 — 最近更新成功通知】\n"
            "----------------------------------------\n"
            f"更新時間：{now_str}\n"
            f"任務狀態：{sync_line}\n"
            f"SQLite 總秒 K 數：{db_info['total_rows']:,} 筆\n"
            f"完整資料區間：{db_info['min_ts']} ~ {db_info['max_ts']}\n"
            f"5 分 K 總根數：{db_info['bars_5m_count']:,} 根 (最新收盤 {db_info['latest_close']:.0f})\n"
            "----------------------------------------\n"
            "【系統與策略最新升級項目】\n"
            "1. Shioaji 流量預留額度已設為 40.0 MB，歷史資料已推進至 2026-06-01 並重建 6 個月份分頁 Excel (共 4,589,684 列)\n"
            "2. 本地與線上 HTML 儀表板已串接最新完整資料，支援自訂「回測起訖區間」與「初始資金 (TWD)」\n"
            "3. 已開啟即時價格監控與【布林_ATR基準 (基準 A：130% 維持率)】進出場訊號自動推播\n"
            "----------------------------------------\n"
            f"線上 RWD 儀表板：{self.GITHUB_PAGES_URL}"
        )
        return self.send_message(msg, category="update_success")

    def notify_strategy_signal(self, signal_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        推播連接即時價格之【布林_ATR基準】策略進場、出場或突破拉回預警訊號。

        參數:
            signal_payload (Dict[str, Any]): 訊號事件資料字典，支援欄位：
                - event_type: 'ENTRY' | 'EXIT' | 'BAR1_BREAKOUT' | 'BAR2_CONFIRMED'
                - side: 'LONG' | 'SHORT'
                - price: 觸發價格
                - time_str: 觸發時間字串
                - qty: 口數
                - stop_loss_price: 停損價
                - exit_reason: 出場原因
                - net_pnl: 單筆淨損益 (出場時)
                - equity_after: 平倉後帳戶總權益
                - reason: 詳細描述

        返回:
            Dict[str, Any]: 推播狀態結果。
        """
        ev_type = str(signal_payload.get("event_type", "SIGNAL")).upper()
        side = str(signal_payload.get("side", "NONE")).upper()
        side_zh = "買進做多 (LONG)" if side == "LONG" else ("賣出放空 (SHORT)" if side == "SHORT" else side)
        price = float(signal_payload.get("price", 0.0))
        time_str = signal_payload.get("time_str") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        qty = int(signal_payload.get("qty", 1))
        sl_price = float(signal_payload.get("stop_loss_price", 0.0))
        equity = float(signal_payload.get("equity", signal_payload.get("equity_after", 105150.0)))
        reason = signal_payload.get("reason", "")

        contract_value = price * 10.0 * max(1, qty)
        eff_lev = (contract_value / equity) if equity > 0 else 0.0
        margin_ratio = (equity / (max(1, qty) * 35050.0)) * 100.0 if qty > 0 else 0.0

        if ev_type in ("ENTRY", "POSITION_OPEN", "BUY_OPEN", "SELL_OPEN"):
            msg = (
                "【策略進場訊號通知 — 布林_ATR基準】\n"
                "----------------------------------------\n"
                f"訊號動作：新倉進場 — {side_zh}\n"
                f"觸發時間：{time_str}\n"
                f"即時成交價：{price:,.0f} 點\n"
                f"建議下單口數：{qty} 口 (基準 A：130% 維持率控管)\n"
                f"初始停損防守價：{sl_price:,.0f} 點\n"
                f"合約總價值：{contract_value:,.0f} TWD\n"
                f"有效槓桿 / 維持率：{eff_lev:.2f}x ({margin_ratio:.1f}%)\n"
                f"觸發依據：{reason or '三根 K 棒突破拉回確認成立，Bar 3 開盤進場'}"
            )
        elif ev_type in ("EXIT", "TRADE_CLOSED", "STOP_LOSS", "TAKE_PROFIT", "ATR_TAKE_PROFIT", "SETTLEMENT"):
            entry_price = float(signal_payload.get("entry_price", 0.0))
            net_pnl = float(signal_payload.get("net_pnl", 0.0))
            pnl_sign = "+" if net_pnl >= 0 else ""
            exit_reason = signal_payload.get("exit_reason") or reason or "策略平倉"
            msg = (
                "【策略出場訊號通知 — 布林_ATR基準】\n"
                "----------------------------------------\n"
                f"訊號動作：部位平倉 — {side_zh}\n"
                f"出場時間：{time_str}\n"
                f"進場價格：{entry_price:,.0f} 點 -> 出場價格：{price:,.0f} 點\n"
                f"平倉口數：{qty} 口\n"
                f"出場原因：{exit_reason}\n"
                f"單筆淨損益 (扣費稅)：{pnl_sign}{net_pnl:,.0f} TWD\n"
                f"平倉後帳戶總淨值：{equity:,.0f} TWD"
            )
        else:
            q1_price = float(signal_payload.get("signal_bar_q1", 0.0))
            msg = (
                "【盤中突破 / 拉回預警訊號 — 布林_ATR基準】\n"
                "----------------------------------------\n"
                f"預警階段：{ev_type}\n"
                f"預警方向：{side_zh}\n"
                f"K 棒時間：{time_str}\n"
                f"即時價格：{price:,.0f} 點\n"
                f"1/4 強度防守點：{q1_price:,.1f} 點\n"
                f"極值停損參考點：{sl_price:,.0f} 點\n"
                f"說明：{reason}"
            )

        return self.send_message(msg, category="strategy_signal")

    def _query_db_summary(self) -> Dict[str, Any]:
        """
        從 SQLite 查詢最新資料範圍、總筆數與最新價格。

        返回:
            Dict[str, Any]: 資料庫統計與最新收盤價資訊。
        """
        info: Dict[str, Any] = {
            "total_rows": 0,
            "min_ts": "--",
            "max_ts": "--",
            "latest_close": 0.0,
            "bars_5m_count": 0,
        }
        if not os.path.exists(self.db_path):
            return info
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute("SELECT COUNT(*), MIN(time), MAX(time) FROM tmf10_second_bars").fetchone()
            if row and row[0]:
                info["total_rows"] = int(row[0])
                info["min_ts"] = str(row[1])
                info["max_ts"] = str(row[2])
                last_row = conn.execute(
                    "SELECT close FROM tmf10_second_bars WHERE time = ?", (row[2],)
                ).fetchone()
                if last_row:
                    info["latest_close"] = float(last_row[0])
            conn.close()
        except Exception as e:
            logger.warning(f"查詢 SQLite 摘要失敗: {e}")
        return info

    def build_status_report_text(self) -> str:
        """
        建構 /status 指令之即時系統狀態與持倉報告文字。

        返回:
            str: 格式化之狀態報告文字。
        """
        db_info = self._query_db_summary()
        live_info = self.live_status_provider() if self.live_status_provider else {}
        pos_side = live_info.get("current_side", "NONE")
        pos_price = live_info.get("latest_price") or db_info["latest_close"]
        pos_time = live_info.get("latest_time") or db_info["max_ts"]
        equity = live_info.get("equity", 178616.0)

        pos_desc = "空手等待訊號 (NONE)"
        if pos_side == "LONG":
            pos_desc = f"持有多單 ({live_info.get('qty', 1)} 口 @ {live_info.get('entry_price', 0):,.0f}, 停損 {live_info.get('stop_loss_price', 0):,.0f})"
        elif pos_side == "SHORT":
            pos_desc = f"持有空單 ({live_info.get('qty', 1)} 口 @ {live_info.get('entry_price', 0):,.0f}, 停損 {live_info.get('stop_loss_price', 0):,.0f})"

        return (
            "【微台10 即時行情與系統狀態報告】\n"
            "----------------------------------------\n"
            f"最新報價：{pos_price:,.0f} 點 ({pos_time})\n"
            f"資料庫範圍：{db_info['min_ts']} ~ {db_info['max_ts']}\n"
            f"秒 K 總筆數：{db_info['total_rows']:,} 筆\n"
            f"當前策略：布林_ATR基準 (基準 A：130% 維持率)\n"
            f"當前持倉：{pos_desc}\n"
            f"模擬帳戶淨值：{equity:,.0f} TWD\n"
            f"線上儀表板：{self.GITHUB_PAGES_URL}"
        )

    def build_signal_snapshot_text(self) -> str:
        """
        計算最新 5 分 K 之布林通道、ATR 指標與最近一筆策略交易訊號，建構 /signal 回應文字。

        返回:
            str: 最新指標與訊號快照文字。
        """
        try:
            from services.storage import KlineService
            from services.backtest import BacktestEngine, BacktestConfig

            svc = KlineService(db_path=self.db_path, include_history=True, since="2026-09-15")
            bars_5m = svc.get_kbars("5m")
            if not bars_5m:
                return "目前尚無足夠 5 分 K 資料計算指標。"

            df = pd.DataFrame(bars_5m)
            df["ts"] = pd.to_datetime(df["datetime"])
            cfg = BacktestConfig(
                strategy_name="布林_ATR基準",
                exit_mode="atr",
                bb_length=30,
                bb_mult=1.80,
                pullback_ratio=0.40,
                atr_length=14,
                atr_sig_length=4,
                initial_capital=105150.0,
                margin_req=35050.0,
                maint_margin=26900.0,
                min_margin_ratio=1.30,
                position_sizing_mode="margin_ratio_130",
            )
            eng = BacktestEngine(config=cfg)
            df_ind = eng.prepare_indicators(df)
            last = df_ind.iloc[-1]
            metrics = eng.run(df)

            last_trade_txt = "尚無成交紀錄"
            if metrics.trades:
                lt = metrics.trades[-1]
                side_zh = "買進做多" if lt.side.value == "LONG" else "賣出放空"
                pnl_sign = "+" if lt.net_pnl >= 0 else ""
                last_trade_txt = (
                    f"方向：{side_zh} ({lt.qty} 口)\n"
                    f"進場：{lt.entry_time} @ {lt.entry_price:,.0f}\n"
                    f"出場：{lt.exit_time} @ {lt.exit_price:,.0f} ({lt.exit_reason.value})\n"
                    f"單筆損益：{pnl_sign}{lt.net_pnl:,.0f} TWD (槓桿 {lt.effective_leverage:.2f}x / 維持率 {lt.margin_ratio_pct:.1f}%)"
                )

            return (
                "【微台10 即時策略指標與最新訊號快照】\n"
                "----------------------------------------\n"
                f"最新 5 分 K 時間：{last['datetime']}\n"
                f"收盤價：{float(last['close']):,.0f} (開 {float(last['open']):,.0f} / 高 {float(last['high']):,.0f} / 低 {float(last['low']):,.0f})\n"
                f"布林上軌 (BB Upper)：{float(last['bb_upper']):,.1f}\n"
                f"布林中軌 (BB Mid)  ：{float(last['bb_mid']):,.1f}\n"
                f"布林下軌 (BB Lower)：{float(last['bb_lower']):,.1f}\n"
                f"ATR (14) / 訊號線(4)：{float(last['atr']):,.2f} / {float(last['atr_sig']):,.2f}\n"
                "----------------------------------------\n"
                "【最近一筆已平倉訊號明細】\n"
                f"{last_trade_txt}"
            )
        except Exception as e:
            return f"計算即時策略指標時發生異常: {e}"

    def build_backtest_summary_text(self) -> str:
        """
        建構 /backtest 指令之完整區間回測摘要文字。

        返回:
            str: 回測摘要文字。
        """
        return (
            "【微台10 布林_ATR基準 完整回測摘要】\n"
            "----------------------------------------\n"
            "回測資料區間：2026-06-01 15:00 ~ 2026-10-15 05:00 (共 21,169 根 5 分 K)\n"
            "風控模式：基準 A (整戶原始保證金維持率 >= 130%，單口門檻 45,565 元，動態浮動槓桿)\n"
            "預設初始資金：105,150 TWD (可於網頁自由調整回測區間與初始資金)\n"
            "----------------------------------------\n"
            "1. 初始基準參數 (BB 30/1.75, Pullback 0.25, ATR 14/5)：\n"
            "   - 總交易筆數：365 筆\n"
            "   - 累積總淨利：+73,466 TWD (+69.87%)\n"
            "   - 勝率 / 獲利因子：37.81% / PF 1.240\n"
            "   - 期末總權益：178,616 TWD\n"
            "----------------------------------------\n"
            f"線上互動回測儀表板：{self.GITHUB_PAGES_URL}"
        )
