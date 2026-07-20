import os
import shioaji as sj
from dotenv import load_dotenv

load_dotenv()
api = sj.Shioaji()
api.login(os.environ["SHIOAJI_API_KEY"], os.environ["SHIOAJI_SECRET_KEY"])

for group in api.Contracts.Futures:
    print(group.category)
    for contract in group:
        if contract.code.startswith("TXF"):
            print(f"  {contract.symbol} - {contract.name} (Code: {contract.code})")

