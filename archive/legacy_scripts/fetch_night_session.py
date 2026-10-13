import os
import asyncio
import shioaji as sj

async def main():
    api = sj.Shioaji()
    api_key = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")
    
    if not api_key or not secret_key:
        print("Status: ERROR | Root Cause: Missing API Key | Suggested Fix: Set SHIOAJI_API_KEY")
        return

    print("Logging in to Shioaji...")
    # 僅抓取報價不需要憑證
    api.login(api_key, secret_key)
    print("Login successful. Fetching contracts...")

    # 取得台指期 (TX) 所有合約
    contracts = list(api.Contracts.Futures.TXF)
    if not contracts:
        print("No TX contracts found.")
        return
        
    # 選擇連續近月合約 (TXFR1) 或第一檔
    target_contract = next((c for c in contracts if c.code == "TXFR1"), contracts[0])
    print(f"Target Contract: {target_contract.symbol} {target_contract.name}")
    
    loop = asyncio.get_event_loop()
    data_received = False
    
    @api.quote.on_tick_fop_v1()
    def quote_callback(exchange, tick):
        nonlocal data_received
        if not data_received:
            print("\n===============================")
            print(f"Success! Received first tick.")
            print(f"Time: {tick.datetime}")
            print(f"Price: {tick.close}")
            print(f"Volume: {tick.volume}")
            print("===============================\n")
            data_received = True

    print("Subscribing to tick data...")
    # 訂閱 Tick 報價
    api.quote.subscribe(target_contract, quote_type=sj.constant.QuoteType.Tick)
    # 防止無窮等待，設定 30 秒 timeout
    print("Waiting for data (night session)...")
    for _ in range(30):
        if data_received:
            break
        await asyncio.sleep(1)
        
    if not data_received:
        print("Timeout waiting for market data. Please ensure the market is open.")
    
    api.logout()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())
