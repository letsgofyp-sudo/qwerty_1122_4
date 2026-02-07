
# ⚠️ Never hardcode secrets; keep them in env vars
SERVICE_KEY = "sk-ws-01-QmnTOgwZ64mN6pMn1IvJfBUFWvwfFvqXsGYDKWNCn5OK5TdkXrN5GjhCcU9de1Bgi3H0jEsQptI6uNWZ6IbKEyyfJJeXVA"

import requests
from datetime import datetime, timedelta, timezone

# Your key (⚠️ rotate if exposed)
# SERVICE_KEY = "sk-ws-01-XXXXXXXXXXXXXXXXXXXXXXXX"

url = "https://server.codeium.com/api/v1/UserPageAnalytics"

# Last 7 days in RFC 3339
now = datetime.now(timezone.utc).replace(microsecond=0)
start = (now - timedelta(days=7)).isoformat().replace("+00:00", "Z")
end = now.isoformat().replace("+00:00", "Z")

payload = {
    "service_key": SERVICE_KEY,
    "start_timestamp": start,
    "end_timestamp": end
}

resp = requests.post(url, json=payload, timeout=30)
print("Status:", resp.status_code)
print("Response:", resp.text)
