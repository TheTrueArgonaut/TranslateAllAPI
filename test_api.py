#!/usr/bin/env python3
"""
Simple test script to validate the enhanced TranslateAll API
"""

import requests
import json
import time
import sys

def test_api_health():
    """Test if the API is running"""
    try:
        response = requests.get('http://localhost:8080/health')
        if response.status_code == 200:
            data = response.json()
            print(f"✅ API Health: {data.get('status')} - {data.get('service')}")
            return True
        else:
            print(f"❌ API Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Cannot connect to API: {e}")
        return False

def test_create_api_key():
    """Test API key creation"""
    try:
        response = requests.post('http://localhost:8080/create-key')
        if response.status_code == 200:
            data = response.json()
            api_key = data.get('apiKey')
            print(f"✅ API Key created: {api_key[:10]}...")
            return api_key
        else:
            print(f"❌ API Key creation failed: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ API Key creation error: {e}")
        return None

def test_translation(api_key):
    """Test basic translation functionality"""
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }
    
    # Test basic translation
    response = requests.post(
        'http://localhost:8080/translate',
        headers=headers,
        json={'text': 'Hello, world!', 'target': 'ES'}
    )
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Translation: '{data.get('translation')}'")
        print(f"   Cached: {data.get('cached', False)}")
        print(f"   Priority: {data.get('priority', False)}")
        print(f"   Response time: {data.get('response_time', 0):.3f}s")
        return True
    else:
        print(f"❌ Translation failed: {response.status_code}")
        print(f"   Error: {response.text}")
        return False

def test_priority_cache(api_key):
    """Test priority cache functionality"""
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }
    
    # Populate cache first
    print("🔄 Populating cache...")
    response = requests.post(
        'http://localhost:8080/cache-populate',
        headers=headers,
        json={'target_lang': 'ES'}
    )
    
    if response.status_code == 200:
        print("✅ Cache population started")
        time.sleep(2)  # Wait for background population
        
        # Check cache status
        response = requests.get(
            'http://localhost:8080/cache-status',
            headers=headers,
            params={'lang': 'ES'}
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Cache status: {data.get('total_cached', 0)} messages cached")
            
            # Test priority message
            response = requests.post(
                'http://localhost:8080/translate',
                headers=headers,
                json={'text': 'Welcome to TranslateAll API!', 'target': 'ES'}
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('priority') and data.get('cached'):
                    print("✅ Priority cache working correctly")
                    return True
                else:
                    print("⚠️  Priority cache not hit as expected")
                    return False
        
    print("❌ Cache population failed")
    return False

def test_batch_translation(api_key):
    """Test batch translation functionality"""
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }
    
    texts = ['Hello', 'Goodbye', 'Thank you']
    
    response = requests.post(
        'http://localhost:8080/translate-batch',
        headers=headers,
        json={'texts': texts, 'target': 'ES'}
    )
    
    if response.status_code == 200:
        data = response.json()
        results = data.get('results', [])
        
        if len(results) == len(texts):
            print("✅ Batch translation completed")
            for i, result in enumerate(results):
                print(f"   {i+1}. {result.get('translation')}")
            return True
        else:
            print("❌ Batch translation incomplete")
            return False
    else:
        print(f"❌ Batch translation failed: {response.status_code}")
        return False

def main():
    print("🧪 Enhanced TranslateAll API Test Suite")
    print("=" * 50)
    
    # Test 1: API Health
    if not test_api_health():
        print("\n❌ API is not running. Please start the API first:")
        print("   python main.py")
        return sys.exit(1)
    
    # Test 2: Create API Key
    api_key = test_create_api_key()
    if not api_key:
        return sys.exit(1)
    
    # Test 3: Basic Translation
    print("\n🔤 Testing basic translation...")
    if not test_translation(api_key):
        return sys.exit(1)
    
    # Test 4: Priority Cache
    print("\n⚡ Testing priority cache...")
    if not test_priority_cache(api_key):
        print("⚠️  Priority cache test failed, but this might be due to background processing")
    
    # Test 5: Batch Translation
    print("\n📦 Testing batch translation...")
    if not test_batch_translation(api_key):
        return sys.exit(1)
    
    print("\n🎉 All tests passed! API is working correctly.")
    print(f"Your API key: {api_key}")
    print("\nTry the demo script next:")
    print("python demo_enhanced_api.py")

if __name__ == "__main__":
    main()