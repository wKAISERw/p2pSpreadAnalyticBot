import asyncio
import os
import dotenv
from pathlib import Path

# Load dotenv to get ENCRYPTION_KEY
dotenv.load_dotenv()

async def test():
    key_str = os.environ.get("ENCRYPTION_KEY", "")
    print(f"key_str: {repr(key_str)}, type: {type(key_str)}, len: {len(key_str)}")
    
    # Try Fernet key check
    from cryptography.fernet import Fernet
    try:
        f = Fernet(key_str.encode())
        print("Fernet init with key_str encode: SUCCESS")
    except Exception as e:
        print(f"Fernet init with key_str encode: FAILED ({e})")
        
    # Check fallback conversion
    try:
        padded = key_str + "=" * (4 - len(key_str) % 4)
        print(f"padded: {repr(padded)}, type: {type(padded)}, len: {len(padded)}")
        
        import base64
        raw = base64.urlsafe_b64decode(padded)[:32]
        print(f"raw: {repr(raw)}, type: {type(raw)}, len: {len(raw)}")
        
        # Test raw.ljust(32, b"\x00")
        res = raw.ljust(32, b"\x00")
        print(f"res: {repr(res)}, type: {type(res)}, len: {len(res)}")
        
    except Exception as e:
        print(f"Fallback path failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
