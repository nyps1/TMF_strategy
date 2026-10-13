import os
import shioaji as sj
from dotenv import load_dotenv

load_dotenv()
api = sj.Shioaji()
api.login(os.environ["SHIOAJI_API_KEY"], os.environ["SHIOAJI_SECRET_KEY"])

print(dir(api.Contracts.Futures))
try:
    print(api.Contracts.Futures.TXF)
except Exception as e:
    print("Error accessing TXF:", e)
