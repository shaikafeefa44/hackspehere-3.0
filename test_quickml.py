
import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()


def env(name, default=""):
    return os.getenv(name, default).strip().strip('"').strip("'")


def mask(value, keep=6):
    if not value:
        return "(empty)"
    return f"{value[:keep]}...({len(value)} chars)"


ENDPOINT = env("QUICKML_ENDPOINT")
TOKEN = env("QUICKML_AUTH_TOKEN")
ENDPOINT_KEY = env("QUICKML_ENDPOINT_KEY")
ORG = env("CATALYST_ORG")
ENVIRONMENT = env("QUICKML_ENVIRONMENT", "Development")

ACCOUNTS_URL = env("ZOHO_ACCOUNTS_URL")
CLIENT_ID = env("ZOHO_CLIENT_ID")
CLIENT_SECRET = env("ZOHO_CLIENT_SECRET")
REFRESH_TOKEN = env("ZOHO_REFRESH_TOKEN")


def refresh_access_token():
    """Get a fresh access token using the refresh token."""
    r = requests.post(
        f"{ACCOUNTS_URL}/oauth/v2/token",
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
        },
        timeout=30,
    )
    data = r.json()
    if "access_token" not in data:
        print("Token refresh FAILED:", r.status_code, data)
        sys.exit(1)
    print("Token refresh OK (new access token issued)")
    return data["access_token"]


print("=== Config check ===")
print("QUICKML_ENDPOINT      :", ENDPOINT or "(empty)")
print("QUICKML_AUTH_TOKEN    :", mask(TOKEN))
print("QUICKML_ENDPOINT_KEY  :", mask(ENDPOINT_KEY))
print("CATALYST_ORG          :", ORG or "(empty)")
print("QUICKML_ENVIRONMENT   :", ENVIRONMENT)
print("Refresh flow configured:", all([ACCOUNTS_URL, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]))

if all([ACCOUNTS_URL, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
    TOKEN = refresh_access_token()

problems = []
if not ENDPOINT:
    problems.append("QUICKML_ENDPOINT is empty")
if not TOKEN:
    problems.append("QUICKML_AUTH_TOKEN is empty")
elif TOKEN.lower().startswith(("zoho-oauthtoken", "bearer")):
    problems.append("QUICKML_AUTH_TOKEN must not include a 'Zoho-oauthtoken' or 'Bearer' prefix")
elif not TOKEN.startswith("1000."):
    problems.append("QUICKML_AUTH_TOKEN does not start with '1000.' - make sure you copied access_token")
if not ENDPOINT_KEY:
    problems.append("QUICKML_ENDPOINT_KEY is empty")
if not ORG:
    problems.append("CATALYST_ORG is empty")

if problems:
    print("\n=== Fix these first ===")
    for p in problems:
        print(" -", p)
    if not ENDPOINT or not TOKEN:
        sys.exit(1)

# Sample payload. Replace the columns with the exact feature columns your model was trained on.
payload = {
    "columns": ["hour"],
    "data": [[10]],
}

headers = {
    "Authorization": f"Zoho-oauthtoken {TOKEN}",
    "X-QUICKML-ENDPOINT-KEY": ENDPOINT_KEY,
    "CATALYST-ORG": ORG,
    "Environment": ENVIRONMENT,
    "Content-Type": "application/json",
}

print("\n=== Sending request ===")
try:
    r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=30)
except requests.RequestException as exc:
    print("Request failed:", exc)
    sys.exit(1)

print("status:", r.status_code)
try:
    print(json.dumps(r.json(), indent=2)[:1500])
except ValueError:
    print(r.text[:1500])

hints = {
    200: "Works. Send the JSON above so predict.py's _extract_predictions() can be matched to it.",
    400: "Auth is fine. The payload does not match the model; use the exact trained feature columns.",
    401: "Token invalid or expired. Generate a new access_token (or set up the refresh-token variables).",
    403: "Permission/scope problem. The scope must be exactly QuickML.deployment.READ.",
    404: "URL, endpoint key or Development/Production setting does not match the published endpoint.",
    422: "Auth is fine. The payload does not match the model; use the exact trained feature columns.",
}
print("\nHint:", hints.get(r.status_code, "Unexpected status; paste the output above."))
