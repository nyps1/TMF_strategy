# 使用官方輕量 Python 3.10 映像檔
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /app

# 設定 Python 環境變數以確保日誌即時輸出且不產生 pyc 檔案
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 安裝基本系統依賴（如 git 或編譯所需，Shioaji 與 asyncpg 安裝時可能需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 複製並安裝 Python 依賴套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案程式碼
COPY . .

# 暴露 HTTP API 監聽埠
EXPOSE 8080

# 預設執行指令
CMD ["python", "-u", "shioaji_client.py"]
