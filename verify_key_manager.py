
import sys
from pathlib import Path
# Add src to path
sys.path.append('src')

from adt_press_backend.key_manager import KeyManager

def test_key_manager():
    print("Initializing KeyManager...")
    # Use a test DB file
    km = KeyManager(db_path="data/test_adt_press.db")
    
    # 1. Create Key
    print("\n1. Creating API Key for 'TestUser' with quota 3...")
    raw_key, record = km.create_key("TestUser", max_generations=3)
    print(f"   Created Key: {raw_key}")
    print(f"   Record: {record}")
    
    assert record['max_generations'] == 3
    assert record['current_generations'] == 0
    
    # 2. Validate Key
    print("\n2. Validating Key...")
    valid_record = km.validate_key(raw_key)
    assert valid_record is not None
    assert valid_record.id == record['id']
    print("   Validation Successful!")
    
    # 3. Check Quota & Increment
    print("\n3. Testing Quota Increment...")
    key_id = record['id']
    
    # Gen 1
    assert km.check_quota(key_id) is True
    assert km.increment_usage(key_id) is True
    print("   Gen 1: Allowed")
    
    # Gen 2
    assert km.increment_usage(key_id) is True
    print("   Gen 2: Allowed")
    
    # Gen 3
    assert km.increment_usage(key_id) is True
    print("   Gen 3: Allowed (Limit Reached)")
    
    # Gen 4 (Should Fail)
    allowed = km.increment_usage(key_id)
    print(f"   Gen 4: {allowed} (Expected False)")
    assert allowed is False
    
    # 4. Revocation
    print("\n4. Testing Revocation...")
    km.revoke_key(key_id)
    valid_record = km.validate_key(raw_key)
    assert valid_record is None
    print("   Revocation Successful (Key no longer validates)")
    
    print("\n✅ All KeyManager tests passed!")

if __name__ == "__main__":
    test_key_manager()
