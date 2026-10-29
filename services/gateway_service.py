import os
import hmac
import requests
from requests.auth import HTTPBasicAuth

# Konfigurasi Xendit
XENDIT_SECRET_KEY = os.getenv("XENDIT_SECRET_KEY")
XENDIT_API_BASE = "https://api.xendit.co"


def create_payment_link(order_id: str, customer_name: str, customer_phone: str, amount: float, item_desc: str):
    external_id = f"WRK-INV-{order_id}"
    url = f"{XENDIT_API_BASE}/v2/invoices"
    
    payload = {
        "external_id": external_id,
        "amount": float(amount),
        "description": f"Additional Cost IndiHome Aura: {item_desc}",
        "customer": {
            "given_names": customer_name,
            "mobile_number": customer_phone
        },
        "success_redirect_url": f"{os.getenv('APP_URL')}/status/{order_id}",
        "failure_redirect_url": f"{os.getenv('APP_URL')}/status/{order_id}",
        "invoice_duration": 86400
    }
    
    try:
        response = requests.post(
            url, 
            json=payload, 
            auth=HTTPBasicAuth(XENDIT_SECRET_KEY, '')
        )
        response.raise_for_status()
        data = response.json()
        
        return {
            "success": True,
            "invoice_id": data.get("id"),
            "external_id": external_id,
            "invoice_url": data.get("invoice_url"),
            "expiry_date": data.get("expiry_date")
        }
    except requests.exceptions.RequestException as e:
        print(f"[Xendit Create Invoice Error] {str(e)}")
        return {"success": False, "error": str(e)}


def verify_webhook_signature(payload: dict, signature: str) -> bool:
    callback_token = os.getenv("XENDIT_CALLBACK_TOKEN")
    if not callback_token:
        print("[WARNING] XENDIT_CALLBACK_TOKEN belum diset di .env")
        return False
    
    return hmac.compare_digest(str(signature), str(callback_token))


def check_payment_status(invoice_id: str):
    url = f"{XENDIT_API_BASE}/v2/invoices/{invoice_id}"
    
    try:
        response = requests.get(
            url, 
            auth=HTTPBasicAuth(XENDIT_SECRET_KEY, '')
        )
        response.raise_for_status()
        data = response.json()
        return data.get("status")
    except requests.exceptions.RequestException as e:
        print(f"[Rekonsiliasi Error] {str(e)}")
        return None
