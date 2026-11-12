import os
from dotenv import load_dotenv, find_dotenv

path = find_dotenv()
print("Using .env file:", path or "NOT FOUND")
load_dotenv(path)

for name in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
    v = os.getenv(name, "")
    print(f"{name}: length={len(v)}, starts={v[:4]!r}, ends={v[-3:]!r}, "
          f"has_space={' ' in v}, has_quote={'\"' in v or chr(39) in v}, "
          f"has_newline={chr(10) in v or chr(13) in v}")
print("PAYPAL_BASE_URL:", os.getenv("PAYPAL_BASE_URL"))