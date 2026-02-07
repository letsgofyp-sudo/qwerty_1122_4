import os
email = os.getenv("SENDER_EMAIL", "")
email_password = os.getenv("SENDER_PASSWORD", "")
BASE_URL  = os.getenv("TEXTBEE_BASE_URL",  "https://api.textbee.dev")
API_KEY   = os.getenv("TEXTBEE_API_KEY",   "")
DEVICE_ID = os.getenv("TEXTBEE_DEVICE_ID", "")
