import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("PERISKOPE_API_KEY")
PHONE = os.getenv("PERISKOPE_PHONE")

url = "https://api.periskope.app/v1/chats"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "x-phone": PHONE,
}

params = {
    "chat_type": "group",   # Only WhatsApp groups
    "limit": 100,           # Increase later if needed
    "offset": 0,
}

response = requests.get(url, headers=headers, params=params)

print("Status:", response.status_code)

if response.status_code == 200:
    data = response.json()
  
    print(f"Total Chats: {data.get('count')}")

    for chat in data.get("chats", []):
        print("-" * 50)
        print("Chat Name :", chat.get("chat_name"))
        print("Chat ID   :", chat.get("chat_id"))
        print("Type      :", chat.get("chat_type"))
else:
    print(response.text)



load_dotenv()
API_KEY = os.getenv("PERISKOPE_API_KEY")
PHONE = os.getenv("PERISKOPE_PHONE")

url = "https://api.periskope.app/v1/chats"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "x-phone": PHONE
}

params = {
    "chat_type": "group",  
    "limit": 100,           
    "offset": 0,
}


response = requests.get(url, headers=headers, params = params)
print("Status: ",response.status_code)


if response.status_code ==200:
    data=response.json()
    print(f"Total chats: {data.get('count')}")

    for chat in data.get("chats", []):
        print("-" * 50)
        print("Chat Name :", chat.get("chat_name"))
        print("Chat ID :", chat.get("chat_id"))
        print("Type :", chat.get("chat_type"))
else:
   print(response.text)