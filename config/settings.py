import os

class Config:
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_USER = os.getenv('DB_USER', '')
    DB_PASS = os.getenv('DB_PASS', '')
    DB_NAME = os.getenv('DB_NAME', '')
    
    GATEWAY_SECRET = os.getenv('GATEWAY_SECRET', '')
    WA_API_KEY = os.getenv('WA_API_KEY', '')
    
    # URL Webhook yang akan didaftarkan ke Payment Gateway
    WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://domainanda.com/webhook/payment')