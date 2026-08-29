import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("PERISKOPE_API_KEY")
PHONE = os.getenv("PERISKOPE_PHONE")

CHAT_ID = "120363428735965864@g.us"

url = f"https://api.periskope.app/v1/chats/{CHAT_ID}/messages"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "x-phone": PHONE,
}

params = {
    "limit": 100,
    "offset": 0,
}

response = requests.get(url, headers=headers, params=params)

print("Status Code:", response.status_code)

if response.status_code == 200:
    data = response.json()

    print(json.dumps(data, indent=2))

    with open("sample_chat.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print("Saved to sample_chat.json")

else:
    print(response.text)


# the messages are in reverse order so we need to reverse the list
for message in reversed(data["messages"]):
    if message["body"] is not None:
        print("-" * 50)
        print(message["body"])
        print("-" * 50)

   