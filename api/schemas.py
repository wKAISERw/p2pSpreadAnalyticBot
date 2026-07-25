# api/schemas.py
from pydantic import BaseModel

class ApiKeyPayload(BaseModel):
    key: str
    secret: str
    passphrase: str = ""

class BlacklistPayload(BaseModel):
    exchange: str
    merchantId: str
    merchantName: str
    reason: str
    source: str

class SessionPayload(BaseModel):
    exchange: str
    user_id: int
    cookies_str: str