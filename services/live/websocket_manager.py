"""
即時 WebSocket 廣播管理器 (services/live/websocket_manager.py)

負責管理所有前端客戶端之全雙工 WebSocket 連線生命週期，
並以非同步廣播方式派發即時行情、K 棒浮動更新、策略訊號與成交明細。

遵循 PEP 8 與 Python Type Hints 規範。
"""

import json
import logging
from typing import Set, Dict, Any, List
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    即時 WebSocket 客戶端連線與推播管理器。
    """

    def __init__(self) -> None:
        """初始化管理器。"""
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """
        接受並註冊新的 WebSocket 客戶端連線。

        參數:
            websocket (WebSocket): 連線實例。
        """
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """
        移除已斷開之客戶端連線。

        參數:
            websocket (WebSocket): 連線實例。
        """
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Remaining connections: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """
        向所有已連線之客戶端廣播 JSON 訊息。
        若連線中途異常中斷則自動移除該客戶端。

        參數:
            message (Dict[str, Any]): 欲發送之字典資料。
        """
        if not self.active_connections:
            return

        text_data = json.dumps(message, ensure_ascii=False)
        disconnected_clients: List[WebSocket] = []

        for connection in self.active_connections:
            try:
                await connection.send_text(text_data)
            except Exception:
                disconnected_clients.append(connection)

        for client in disconnected_clients:
            self.disconnect(client)

    @property
    def client_count(self) -> int:
        """取得當前在線客戶端數量。"""
        return len(self.active_connections)
