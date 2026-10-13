import urllib.request
import os

url = "https://files.pythonhosted.org/packages/49/71/ca1ed82d5f0e9fca2123d8c1c4f52de287e008a0d4db0264bfa136d80ff5/shioaji-1.5.6-cp37-abi3-win_amd64.whl"
filename = "shioaji-1.5.6-cp37-abi3-win_amd64.whl"

print(f"Downloading {filename}...")
try:
    urllib.request.urlretrieve(url, filename)
    print("Download completed!")
except Exception as e:
    print(f"Failed: {e}")
