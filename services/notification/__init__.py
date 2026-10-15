"""
通知與訊息推播服務模組 (services/notification/__init__.py)

提供 Telegram Bot 即時推播、訂閱者持久化管理、待發送佇列與互動指令處理服務。
遵循 PEP 8 與 Python Type Hints 規範。
"""

from .telegram_bot import TelegramBotService

__all__ = ["TelegramBotService"]
