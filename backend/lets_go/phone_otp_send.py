import requests
import sys
import os
from requests.exceptions import HTTPError, Timeout
import random
import string

BASE_URL  = os.getenv("TEXTBEE_BASE_URL",  "https://api.textbee.dev")
API_KEY   = os.getenv("TEXTBEE_API_KEY",   "")
DEVICE_ID = os.getenv("TEXTBEE_DEVICE_ID", "")
# Endpoint path template

SEND_SMS_PATH = "/api/v1/gateway/devices/{device_id}/send-sms"

def send_phone_otp(phone_number:str, otp_code: str) -> bool:
    """
    Send a single SMS via TextBee.
    :param phone_number: E.164 format, e.g. "+923316963802"
    :param otp_code:      The OTP code to send, e.g. "3453"
    :returns: True on HTTP 2xx, False on failure.
    """
    url = f"{BASE_URL}{SEND_SMS_PATH.format(device_id=DEVICE_ID)}"
    headers = {
        "x-api-key":    API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "recipients": [ phone_number ],
        "message":    f"Your OTP is {otp_code}",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()  # raises HTTPError for 4xx/5xx
        print(f"✅ SMS sent! Response: {resp.json()}")
        return True
    except (HTTPError, Timeout) as err:
        print(f"❌ Failed to send SMS: {err} – Response body: {getattr(err, 'response', None)}")
        return False
    except Exception as err:
        print(f"❌ An unexpected error occurred: {err}")
        return False
    

def send_phone_otp_for_reset(phone_number: str, otp_code: str) -> bool:
    """
    Sends a password reset OTP via SMS using TextBee.
    """
    url = f"{BASE_URL}{SEND_SMS_PATH.format(device_id=DEVICE_ID)}"
    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "recipients": [phone_number],
        "message": f"You requested to reset your password. Your OTP is {otp_code}.",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
        print(f"✅ Reset password SMS sent! Response: {resp.json()}")
        return True
    except (HTTPError, Timeout) as err:
        print(f"❌ Failed to send reset SMS: {err}")
        return False
    except Exception as err:
        print(f"❌ Unexpected error while sending reset SMS: {err}")
        return False
