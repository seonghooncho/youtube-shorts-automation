
import requests
import os
from dotenv import load_dotenv

load_dotenv()
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

def send_slack_message(message):
    if not SLACK_WEBHOOK_URL:
        print(f"Slack webhook not configured. Message: {message}")
        return
    payload = {"text": message}
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 200:
            print("Slack notification sent.")
        else:
            print("Slack notification failed.", response.text)
    except requests.RequestException as e:
        print(f"Slack notification failed: {e}")
