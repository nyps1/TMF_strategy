# 環境依賴 (Dependencies)

本專案之 Python 核心連線模組依賴以下外部套件，所有版本號皆已經過固定以確保系統在重製或部署時的一致性與穩定性。

| Package Name | Version | Purpose |
|--------------|---------|---------|
| shioaji      | == 1.3.1| 永豐 API，負責建立 TCP/WebSocket 連線與接收即時報價資料。 |
| aiosqlite    | == 0.20.0| 非同步 SQLite 驅動，確保資料寫入不阻塞 Event Loop，提供更佳的高頻寫入效能。 |
| pydantic     | == 2.7.1| 資料驗證與強型別控制，確保從外部 API 收到的報價結構完整性，避免後續處理崩潰。 |

## 安裝指令

請於虛擬環境中執行以下指令以安裝依賴：

```bash
pip install -r requirements.txt
```

若您尚未建立 `requirements.txt`，亦可直接執行以下指令：

```bash
pip install shioaji==1.3.1 aiosqlite==0.20.0 pydantic==2.7.1
```
