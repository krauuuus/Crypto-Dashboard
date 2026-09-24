import sys, io, requests
from zipfile import ZipFile

sys.path.insert(0, ".")

# Test direct (sans la fonction wrapper)
url = "https://www.bis.org/pages/download-central-bankers-speeches/speeches-2025.zip"
resp = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
resp.raise_for_status()
z = ZipFile(io.BytesIO(resp.content))
print("ZIP contents:", z.namelist())

import pandas as pd
with z.open(z.namelist()[0]) as f:
    content = f.read().decode("utf-8", errors="ignore")
    df = pd.read_csv(io.StringIO(content))

print("Shape  :", df.shape)
print("Columns:", df.columns.tolist())
print(df.head(2).to_string())
