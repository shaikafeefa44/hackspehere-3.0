import requests

ACCOUNTS = "https://accounts.zoho.in"    
CLIENT_ID = "1000.VM9V9X85Z8WLADF8XZPZMFDGK562OK"
CLIENT_SECRET = "fdb3613b3b0f6c8651311b60864d6f8495e155f69c"
GRANT_CODE = "1000.eef8df9c003b4862dec3665f3c97f5c6.f25c7d774310e3d2b0b5d39f4d549585"

r = requests.post(f"{ACCOUNTS}/oauth/v2/token", data={
    "grant_type": "authorization_code",
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": GRANT_CODE,
})
print(r.status_code)
print(r.json())