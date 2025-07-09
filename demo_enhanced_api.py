#!/usr/bin/env python3
"""
Enhanced TranslateAll API Demo Script
Demonstrates the new translation pipeline features including:
- Priority caching for instant responses
- Batch translation processing
- Performance monitoring
- Cache population and status checking
"""

import requests
import json
import time
from typing import List, Dict

# Demo configuration
API_BASE_URL = "http://localhost:8080"
DEMO_API_KEY = "your_api_key_here"  # Replace with your actual API key

class EnhancedTranslationDemo:
    def __init__(self, api_key: str, base_url: str = API_BASE_URL):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            'X-API-KEY': api_key,
            'Content-Type': 'application/json'
        }
    
    def translate_single(self, text: str, target_lang: str = 'ES') -> Dict:
        """Test single translation with performance metrics"""
        print(f"\n🔸 Translating: '{text}' to {target_lang}")
        start_time = time.time()
        
        response = requests.post(
            f"{self.base_url}/translate",
            headers=self.headers,
            json={'text': text, 'target': target_lang}
        )
        
        end_time = time.time()
        
        if response.status_code == 200:
            data = response.json()
            cached_indicator = "⚡ CACHED" if data.get('cached') else "🌐 API"
            priority_indicator = "🟢 PRIORITY" if data.get('priority') else "🔵 REGULAR"
            
            print(f"   ✅ {cached_indicator} {priority_indicator}")
            print(f"   📝 Result: {data.get('translation')}")
            print(f"   ⏱️  Response time: {data.get('response_time', end_time - start_time):.3f}s")
            return data
        else:
            print(f"   ❌ Error: {response.status_code}")
            print(f"   📝 Message: {response.text}")
            return {'success': False, 'error': response.text}
    
    def translate_batch(self, texts: List[str], target_lang: str = 'ES') -> Dict:
        """Test batch translation"""
        print(f"\n📦 Batch translating {len(texts)} texts to {target_lang}")
        start_time = time.time()
        
        response = requests.post(
            f"{self.base_url}/translate-batch",
            headers=self.headers,
            json={'texts': texts, 'target': target_lang}
        )
        
        end_time = time.time()
        
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            
            print(f"   ✅ Batch completed in {end_time - start_time:.3f}s")
            for i, result in enumerate(results):
                cached_indicator = "⚡" if result.get('cached') else "🌐"
                print(f"   {i+1}. {cached_indicator} {result.get('translation')}")
            
            return data
        else:
            print(f"   ❌ Batch error: {response.status_code}")
            return {'success': False, 'error': response.text}
    
    def populate_cache(self, target_lang: str = 'ES') -> Dict:
        """Populate priority cache for a language"""
        print(f"\n🔄 Populating cache for {target_lang}")
        
        response = requests.post(
            f"{self.base_url}/cache-populate",
            headers=self.headers,
            json={'target_lang': target_lang}
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Cache population {data.get('status')}")
            return data
        else:
            print(f"   ❌ Cache population error: {response.status_code}")
            return {'success': False, 'error': response.text}
    
    def check_cache_status(self, target_lang: str = 'ES') -> Dict:
        """Check cache status for a language"""
        print(f"\n📊 Checking cache status for {target_lang}")
        
        response = requests.get(
            f"{self.base_url}/cache-status",
            headers=self.headers,
            params={'lang': target_lang}
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"   📈 Total cached: {data.get('total_cached', 0)}")
            print(f"   🟢 Priority 1 (Critical): {data.get('priority_1', 0)}")
            print(f"   🟡 Priority 2 (Common): {data.get('priority_2', 0)}")
            print(f"   🔄 Status: {data.get('populate_status', 'unknown')}")
            print(f"   ✅ Cache ready: {data.get('cache_ready', False)}")
            return data
        else:
            print(f"   ❌ Cache status error: {response.status_code}")
            return {'success': False, 'error': response.text}
    
    def get_performance_metrics(self) -> Dict:
        """Get performance metrics"""
        print(f"\n📊 Performance Metrics")
        
        response = requests.get(
            f"{self.base_url}/performance-metrics",
            headers=self.headers
        )
        
        if response.status_code == 200:
            data = response.json()
            
            for metric_type, stats in data.items():
                print(f"   📈 {metric_type.replace('_', ' ').title()}:")
                print(f"      Count: {stats.get('count', 0)}")
                print(f"      Avg time: {stats.get('avg_time', 0):.3f}s")
                print(f"      Min time: {stats.get('min_time', 0):.3f}s")
                print(f"      Max time: {stats.get('max_time', 0):.3f}s")
            
            return data
        else:
            print(f"   ❌ Metrics error: {response.status_code}")
            return {'success': False, 'error': response.text}

def run_comprehensive_demo():
    """Run a comprehensive demo of all enhanced features"""
    print("🚀 Enhanced TranslateAll API Demo")
    print("=" * 50)
    
    # Initialize demo client
    demo = EnhancedTranslationDemo(DEMO_API_KEY)
    
    # Test 1: Check initial cache status
    print("\n🔍 STEP 1: Initial Cache Status")
    demo.check_cache_status('ES')
    
    # Test 2: Populate cache
    print("\n🔄 STEP 2: Populate Cache")
    demo.populate_cache('ES')
    time.sleep(2)  # Wait for background population
    
    # Test 3: Check cache status after population
    print("\n🔍 STEP 3: Cache Status After Population")
    demo.check_cache_status('ES')
    
    # Test 4: Test priority message translations (should be instant)
    print("\n⚡ STEP 4: Priority Message Translations")
    priority_messages = [
        "Welcome to TranslateAll API!",
        "Translation completed successfully.",
        "An error occurred. Please try again.",
        "Processing your translation...",
        "Hello",
        "Thank you",
        "Help"
    ]
    
    for message in priority_messages:
        demo.translate_single(message, 'ES')
        time.sleep(0.1)  # Small delay to see the difference
    
    # Test 5: Test regular translations (will be cached after first use)
    print("\n🔵 STEP 5: Regular Translations")
    regular_messages = [
        "The weather is beautiful today.",
        "I love programming with Python.",
        "This is an amazing translation service.",
        "Machine learning is fascinating."
    ]
    
    for message in regular_messages:
        demo.translate_single(message, 'ES')
        time.sleep(0.1)
    
    # Test 6: Test the same regular translations again (should be cached)
    print("\n⚡ STEP 6: Cached Regular Translations")
    for message in regular_messages:
        demo.translate_single(message, 'ES')
        time.sleep(0.1)
    
    # Test 7: Batch translation
    print("\n📦 STEP 7: Batch Translation")
    batch_texts = [
        "Good morning",
        "How are you?",
        "See you later",
        "Have a great day",
        "Thank you very much"
    ]
    demo.translate_batch(batch_texts, 'ES')
    
    # Test 8: Performance metrics
    print("\n📊 STEP 8: Performance Metrics")
    demo.get_performance_metrics()
    
    # Test 9: Test different language
    print("\n🌍 STEP 9: Test Different Language (French)")
    demo.populate_cache('FR')
    time.sleep(2)
    demo.translate_single("Hello", 'FR')
    demo.translate_single("Welcome to TranslateAll API!", 'FR')
    
    print("\n🎉 Demo completed successfully!")
    print("=" * 50)

def run_performance_comparison():
    """Compare performance between priority cache and regular translations"""
    print("\n⚡ Performance Comparison Demo")
    print("=" * 40)
    
    demo = EnhancedTranslationDemo(DEMO_API_KEY)
    
    # Ensure cache is populated
    demo.populate_cache('ES')
    time.sleep(3)
    
    # Test priority cache performance
    print("\n🟢 Priority Cache Performance:")
    priority_times = []
    for _ in range(5):
        start = time.time()
        result = demo.translate_single("Welcome to TranslateAll API!", 'ES')
        end = time.time()
        if result.get('success'):
            priority_times.append(end - start)
    
    # Test regular translation performance
    print("\n🔵 Regular Translation Performance:")
    regular_times = []
    test_texts = [
        "This is a test message number 1",
        "This is a test message number 2", 
        "This is a test message number 3",
        "This is a test message number 4",
        "This is a test message number 5"
    ]
    
    for text in test_texts:
        start = time.time()
        result = demo.translate_single(text, 'ES')
        end = time.time()
        if result.get('success'):
            regular_times.append(end - start)
    
    # Compare results
    print(f"\n📊 Performance Comparison:")
    if priority_times:
        avg_priority = sum(priority_times) / len(priority_times)
        print(f"   ⚡ Priority Cache Avg: {avg_priority:.3f}s")
    
    if regular_times:
        avg_regular = sum(regular_times) / len(regular_times)
        print(f"   🔵 Regular Translation Avg: {avg_regular:.3f}s")
    
    if priority_times and regular_times:
        improvement = ((avg_regular - avg_priority) / avg_regular) * 100
        print(f"   🚀 Performance improvement: {improvement:.1f}%")

if __name__ == "__main__":
    print("Choose demo mode:")
    print("1. Comprehensive Demo (all features)")
    print("2. Performance Comparison")
    print("3. Both")
    
    choice = input("Enter choice (1/2/3): ").strip()
    
    if choice == "1":
        run_comprehensive_demo()
    elif choice == "2":
        run_performance_comparison()
    elif choice == "3":
        run_comprehensive_demo()
        run_performance_comparison()
    else:
        print("Invalid choice. Running comprehensive demo...")
        run_comprehensive_demo()