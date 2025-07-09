from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from flask_cors import CORS
import requests
import os
import stripe
import sqlite3
import uuid
import json
import time
import hashlib
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import boto3
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
import threading
import asyncio
from collections import defaultdict

# Set default AWS region if not provided
if not os.getenv('AWS_REGION'):
    os.environ['AWS_REGION'] = 'us-east-1'

# Set DeepL API key if not provided
if not os.getenv('DEEPL_API_KEY'):
    os.environ['DEEPL_API_KEY'] = '74732027-e377-4323-8a86-2744ab7ae7ca'

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret')
CORS(app)

# Configuration with better error handling
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
if not DEEPL_API_KEY:
    print("⚠️  WARNING: DEEPL_API_KEY not set - translation will fail!")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
if not stripe.api_key:
    print("⚠️  WARNING: STRIPE_SECRET_KEY not set - payments will fail!")

stripe_publishable_key = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
stripe_price_id = os.getenv("STRIPE_PRICE_ID", "")

# Enhanced Configuration for Translation Pipeline
TRANSLATION_RATE_LIMIT = 10  # requests per second
BATCH_SIZE = 5
CACHE_EXPIRY_HOURS = 24
PRIORITY_CACHE_SIZE = 50

# IP Rate Limiting Configuration (only for actual API calls)
IP_RATE_LIMITS = {
    'demo': {
        'per_minute': 5,      # 5 DeepL API calls per minute per IP
        'per_hour': 20,       # 20 DeepL API calls per hour per IP  
        'per_day': 100        # 100 DeepL API calls per day per IP
    },
    'paid': {
        'per_minute': 30,     # More generous for paid users
        'per_hour': 200,      
        'per_day': 2000
    }
}

@app.context_processor
def inject_now():
    return {'now': datetime.now(timezone.utc)}

# ==== IP RATE LIMITING SYSTEM ====

class IPRateLimiter:
    """IP-based rate limiting for actual DeepL API calls only"""
    
    def __init__(self):
        self.init_rate_limit_db()
    
    def init_rate_limit_db(self):
        """Initialize IP rate limiting database table"""
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS ip_rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            endpoint_type TEXT NOT NULL,
            minute_count INTEGER DEFAULT 0,
            hour_count INTEGER DEFAULT 0,
            day_count INTEGER DEFAULT 0,
            minute_reset TIMESTAMP NOT NULL,
            hour_reset TIMESTAMP NOT NULL,
            day_reset TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ip_address, endpoint_type)
        )''')
        conn.commit()
        conn.close()
    
    def get_client_ip(self, request):
        """Get client IP address, handling proxies"""
        # Check for forwarded IPs (common in production)
        forwarded_ips = request.headers.get('X-Forwarded-For')
        if forwarded_ips:
            return forwarded_ips.split(',')[0].strip()
        
        # Check for real IP (some proxies)
        real_ip = request.headers.get('X-Real-IP')
        if real_ip:
            return real_ip
        
        # Fall back to remote address
        return request.remote_addr or '127.0.0.1'
    
    def check_and_update_rate_limit(self, request, endpoint_type='demo', increment=True):
        """
        Check if IP is within rate limits and optionally increment counter
        Returns: (allowed: bool, remaining_calls: dict, reset_times: dict)
        """
        ip_address = self.get_client_ip(request)
        limits = IP_RATE_LIMITS.get(endpoint_type, IP_RATE_LIMITS['demo'])
        
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        
        now = datetime.now(timezone.utc)
        
        # Calculate reset times
        minute_reset = now + timedelta(minutes=1)
        hour_reset = now + timedelta(hours=1) 
        day_reset = now + timedelta(days=1)
        
        # Get or create IP record
        c.execute('''SELECT minute_count, hour_count, day_count, 
                            minute_reset, hour_reset, day_reset 
                     FROM ip_rate_limits 
                     WHERE ip_address = ? AND endpoint_type = ?''', 
                 (ip_address, endpoint_type))
        row = c.fetchone()
        
        if not row:
            # First time seeing this IP for this endpoint
            if increment:
                c.execute('''INSERT INTO ip_rate_limits 
                            (ip_address, endpoint_type, minute_count, hour_count, day_count,
                             minute_reset, hour_reset, day_reset) 
                            VALUES (?, ?, 1, 1, 1, ?, ?, ?)''',
                         (ip_address, endpoint_type, minute_reset, hour_reset, day_reset))
                conn.commit()
            
            conn.close()
            return True, {
                'minute': limits['per_minute'] - (1 if increment else 0),
                'hour': limits['per_hour'] - (1 if increment else 0),
                'day': limits['per_day'] - (1 if increment else 0)
            }, {
                'minute': minute_reset.isoformat(),
                'hour': hour_reset.isoformat(), 
                'day': day_reset.isoformat()
            }
        
        minute_count, hour_count, day_count, minute_reset_db, hour_reset_db, day_reset_db = row
        
        # Convert string timestamps back to datetime
        minute_reset_db = datetime.fromisoformat(minute_reset_db.replace('Z', '+00:00'))
        hour_reset_db = datetime.fromisoformat(hour_reset_db.replace('Z', '+00:00'))  
        day_reset_db = datetime.fromisoformat(day_reset_db.replace('Z', '+00:00'))
        
        # Reset counters if time windows have passed
        if now >= minute_reset_db:
            minute_count = 0
            minute_reset_db = now + timedelta(minutes=1)
        
        if now >= hour_reset_db:
            hour_count = 0
            hour_reset_db = now + timedelta(hours=1)
            
        if now >= day_reset_db:
            day_count = 0
            day_reset_db = now + timedelta(days=1)
        
        # Check limits
        if minute_count >= limits['per_minute']:
            conn.close()
            return False, {
                'minute': 0,
                'hour': max(0, limits['per_hour'] - hour_count),
                'day': max(0, limits['per_day'] - day_count)
            }, {
                'minute': minute_reset_db.isoformat(),
                'hour': hour_reset_db.isoformat(),
                'day': day_reset_db.isoformat()
            }
        
        if hour_count >= limits['per_hour']:
            conn.close()
            return False, {
                'minute': max(0, limits['per_minute'] - minute_count),
                'hour': 0,
                'day': max(0, limits['per_day'] - day_count)
            }, {
                'minute': minute_reset_db.isoformat(),
                'hour': hour_reset_db.isoformat(),
                'day': day_reset_db.isoformat()
            }
            
        if day_count >= limits['per_day']:
            conn.close()
            return False, {
                'minute': max(0, limits['per_minute'] - minute_count),
                'hour': max(0, limits['per_hour'] - hour_count),
                'day': 0
            }, {
                'minute': minute_reset_db.isoformat(),
                'hour': hour_reset_db.isoformat(),
                'day': day_reset_db.isoformat()
            }
        
        # Increment counters if requested
        if increment:
            minute_count += 1
            hour_count += 1
            day_count += 1
            
            c.execute('''UPDATE ip_rate_limits 
                        SET minute_count = ?, hour_count = ?, day_count = ?,
                            minute_reset = ?, hour_reset = ?, day_reset = ?
                        WHERE ip_address = ? AND endpoint_type = ?''',
                     (minute_count, hour_count, day_count,
                      minute_reset_db, hour_reset_db, day_reset_db,
                      ip_address, endpoint_type))
            conn.commit()
        
        conn.close()
        
        return True, {
            'minute': limits['per_minute'] - minute_count,
            'hour': limits['per_hour'] - hour_count,
            'day': limits['per_day'] - day_count
        }, {
            'minute': minute_reset_db.isoformat(),
            'hour': hour_reset_db.isoformat(),
            'day': day_reset_db.isoformat()
        }
    
    def get_rate_limit_info(self, request, endpoint_type='demo'):
        """Get current rate limit status without incrementing"""
        allowed, remaining, reset_times = self.check_and_update_rate_limit(
            request, endpoint_type, increment=False
        )
        
        return {
            'allowed': allowed,
            'remaining': remaining,
            'reset_times': reset_times,
            'limits': IP_RATE_LIMITS.get(endpoint_type, IP_RATE_LIMITS['demo'])
        }

# Initialize rate limiter
ip_rate_limiter = IPRateLimiter()

# ==== ENHANCED TRANSLATION PIPELINE COMPONENTS ====

class PriorityCacheManager:
    """Manages priority-based caching for critical translation messages"""
    
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.cache_lock = threading.Lock()
        self.populate_status = {}
        self.init_priority_cache_db()
    
    def init_priority_cache_db(self):
        """Initialize priority cache database table"""
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS priority_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT NOT NULL,
            target_lang TEXT NOT NULL,
            translation TEXT NOT NULL,
            priority INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            uses INTEGER DEFAULT 0,
            UNIQUE(cache_key, target_lang)
        )''')
        
        # Create translation cache table for regular translations
        c.execute('''CREATE TABLE IF NOT EXISTS translation_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text_hash TEXT NOT NULL,
            source_text TEXT NOT NULL,
            target_lang TEXT NOT NULL,
            translation TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            uses INTEGER DEFAULT 0,
            UNIQUE(text_hash, target_lang)
        )''')
        
        conn.commit()
        conn.close()
    
    def get_critical_messages(self) -> Dict[str, str]:
        """Critical messages that need instant translation"""
        return {
            "welcome": "Welcome to TranslateAll API!",
            "error_general": "An error occurred. Please try again.",
            "success_general": "Translation completed successfully.",
            "rate_limit": "Rate limit exceeded. Please try again later.",
            "invalid_key": "Invalid API key provided.",
            "quota_exceeded": "Translation quota exceeded.",
            "network_error": "Network connection error.",
            "processing": "Processing your translation...",
            "completed": "Translation completed!",
            "failed": "Translation failed. Please try again.",
            "timeout": "Translation timeout. Please try again.",
            "service_unavailable": "Service temporarily unavailable.",
            "invalid_text": "Invalid text provided for translation.",
            "language_not_supported": "Language not supported.",
            "text_too_long": "Text too long for translation."
        }
    
    def get_common_responses(self) -> Dict[str, str]:
        """Common responses for priority 2 caching"""
        return {
            "hello": "Hello",
            "goodbye": "Goodbye",
            "thank_you": "Thank you",
            "please": "Please",
            "yes": "Yes",
            "no": "No",
            "help": "Help",
            "cancel": "Cancel",
            "confirm": "Confirm",
            "save": "Save",
            "delete": "Delete",
            "edit": "Edit",
            "search": "Search",
            "filter": "Filter",
            "sort": "Sort",
            "loading": "Loading...",
            "waiting": "Please wait...",
            "done": "Done",
            "continue": "Continue",
            "back": "Back",
            "next": "Next",
            "previous": "Previous",
            "home": "Home",
            "settings": "Settings",
            "profile": "Profile",
            "logout": "Logout",
            "login": "Login",
            "register": "Register",
            "forgot_password": "Forgot Password",
            "reset_password": "Reset Password",
            "change_password": "Change Password"
        }
    
    def populate_priority_cache(self, target_lang: str) -> str:
        """Populate priority cache in background"""
        if target_lang in self.populate_status:
            return self.populate_status[target_lang]
        
        self.populate_status[target_lang] = "started"
        
        def populate():
            try:
                critical_messages = self.get_critical_messages()
                common_responses = self.get_common_responses()
                
                conn = sqlite3.connect('api_keys.db')
                c = conn.cursor()
                
                # Priority 1: Critical messages
                for key, message in critical_messages.items():
                    translation = self._translate_with_deepl(message, target_lang)
                    if translation:
                        expires_at = datetime.now(timezone.utc) + timedelta(hours=CACHE_EXPIRY_HOURS)
                        c.execute('''INSERT OR REPLACE INTO priority_cache 
                                    (cache_key, target_lang, translation, priority, expires_at) 
                                    VALUES (?, ?, ?, ?, ?)''',
                                (key, target_lang, translation, 1, expires_at))
                    time.sleep(0.1)  # Rate limiting
                
                # Priority 2: Common responses
                for key, message in common_responses.items():
                    translation = self._translate_with_deepl(message, target_lang)
                    if translation:
                        expires_at = datetime.now(timezone.utc) + timedelta(hours=CACHE_EXPIRY_HOURS)
                        c.execute('''INSERT OR REPLACE INTO priority_cache 
                                    (cache_key, target_lang, translation, priority, expires_at) 
                                    VALUES (?, ?, ?, ?, ?)''',
                                (key, target_lang, translation, 2, expires_at))
                    time.sleep(0.1)  # Rate limiting
                
                conn.commit()
                conn.close()
                
                self.populate_status[target_lang] = "completed"
                
            except Exception as e:
                self.populate_status[target_lang] = f"failed: {str(e)}"
        
        self.executor.submit(populate)
        return "started"
    
    def _translate_with_deepl(self, text: str, target_lang: str) -> Optional[str]:
        """Internal method to translate with DeepL API"""
        if not DEEPL_API_KEY:
            print("❌ DeepL API key not configured")
            return None
            
        try:
            resp = requests.post(
                'https://api.deepl.com/v2/translate',
                headers={'Authorization': f'DeepL-Auth-Key {DEEPL_API_KEY}'},
                data={'text': text, 'target_lang': target_lang},
                timeout=10
            )
            
            if resp.status_code == 200:
                return resp.json()['translations'][0]['text']
            elif resp.status_code == 403:
                print(f"❌ DeepL API 403 Forbidden - Check your API key and quota")
                print(f"   Response: {resp.text}")
                return None
            elif resp.status_code == 456:
                print(f"❌ DeepL API 456 Quota Exceeded")
                return None
            else:
                print(f"❌ DeepL API error {resp.status_code}: {resp.text}")
                return None
            
        except Exception as e:
            print(f"DeepL translation error: {e}")
            return None
    
    def get_cached_translation(self, key: str, target_lang: str) -> Optional[str]:
        """Get cached translation for a priority message"""
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        
        # Check if cache is still valid
        now = datetime.now(timezone.utc)
        c.execute('''SELECT translation FROM priority_cache 
                     WHERE cache_key=? AND target_lang=? AND expires_at > ?''', 
                 (key, target_lang, now))
        result = c.fetchone()
        
        if result:
            # Update usage counter
            c.execute('UPDATE priority_cache SET uses = uses + 1 WHERE cache_key=? AND target_lang=?',
                     (key, target_lang))
            conn.commit()
        
        conn.close()
        return result[0] if result else None
    
    def get_cache_status(self, target_lang: str) -> Dict[str, any]:
        """Get cache status for a language"""
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        
        c.execute('SELECT priority, COUNT(*) FROM priority_cache WHERE target_lang=? GROUP BY priority',
                 (target_lang,))
        priority_counts = dict(c.fetchall())
        
        c.execute('SELECT COUNT(*) FROM priority_cache WHERE target_lang=?', (target_lang,))
        total_cached = c.fetchone()[0]
        
        conn.close()
        
        return {
            'language': target_lang,
            'total_cached': total_cached,
            'priority_1': priority_counts.get(1, 0),
            'priority_2': priority_counts.get(2, 0),
            'populate_status': self.populate_status.get(target_lang, 'not_started'),
            'cache_ready': total_cached >= 10
        }

class TranslationBatcher:
    """Handles batch translation processing with rate limiting and smart IP-based DeepL call limiting"""
    
    def __init__(self, rate_limit: int = TRANSLATION_RATE_LIMIT):
        self.rate_limit = rate_limit
        self.batch_size = BATCH_SIZE
        self.last_request_time = 0
        self.request_count = 0
        self.rate_lock = threading.Lock()
    
    def _rate_limit_check(self):
        """Implement classic synthetic rate limiting (in addition to IP-based for DeepL calls)"""
        with self.rate_lock:
            current_time = time.time()
            if current_time - self.last_request_time >= 1.0:
                self.request_count = 0
                self.last_request_time = current_time
            
            if self.request_count >= self.rate_limit:
                sleep_time = 1.0 - (current_time - self.last_request_time)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                self.request_count = 0
                self.last_request_time = time.time()
            
            self.request_count += 1
    
    def _get_text_hash(self, text: str) -> str:
        """Generate hash for text caching"""
        return hashlib.md5(text.encode()).hexdigest()
    
    def _get_cached_translation(self, text: str, target_lang: str) -> Optional[str]:
        """Check for cached translation"""
        text_hash = self._get_text_hash(text)
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        
        now = datetime.now(timezone.utc)
        c.execute('''SELECT translation FROM translation_cache 
                     WHERE text_hash=? AND target_lang=? AND expires_at > ?''',
                 (text_hash, target_lang, now))
        result = c.fetchone()
        
        if result:
            # Update usage counter
            c.execute('UPDATE translation_cache SET uses = uses + 1 WHERE text_hash=? AND target_lang=?',
                     (text_hash, target_lang))
            conn.commit()
        
        conn.close()
        return result[0] if result else None
    
    def _cache_translation(self, text: str, target_lang: str, translation: str):
        """Cache translation result"""
        text_hash = self._get_text_hash(text)
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        
        expires_at = datetime.now(timezone.utc) + timedelta(hours=CACHE_EXPIRY_HOURS)
        c.execute('''INSERT OR REPLACE INTO translation_cache 
                     (text_hash, source_text, target_lang, translation, expires_at) 
                     VALUES (?, ?, ?, ?, ?)''',
                 (text_hash, text, target_lang, translation, expires_at))
        
        conn.commit()
        conn.close()
    
    def translate_single(self, text: str, target_lang: str, request=None, api_key=None, endpoint_type='demo') -> Dict[str, any]:
        """Translate single text with caching and rate limiting and DeepL IP call limiting
        NOTE: request and api_key are only required for IP-based rate limiting.
        """
        start_time = time.time()
        
        # Check cache first
        cached_translation = self._get_cached_translation(text, target_lang)
        if cached_translation:
            return {
                'success': True,
                'translation': cached_translation,
                'cached': True,
                'response_time': time.time() - start_time
            }
        
        # Only if NOT cached, we check IP DeepL call limits
        if request:
            allowed, remaining, reset_times = ip_rate_limiter.check_and_update_rate_limit(
                request, endpoint_type, increment=True
            )
            if not allowed:
                return {
                    'success': False,
                    'error': f"Rate limit exceeded for DeepL API calls from your IP. Limit resets at: {reset_times}",
                    'ip_rate_limit': {
                        'allowed': False,
                        'remaining': remaining,
                        'reset_times': reset_times,
                        'limits': IP_RATE_LIMITS.get(endpoint_type, IP_RATE_LIMITS['demo'])
                    },
                    'cached': False,
                    'response_time': time.time() - start_time
                }
        
        # Apply synthetic rate limiting guard (simple per-process)
        self._rate_limit_check()
        
        # Translate with DeepL
        try:
            resp = requests.post(
                'https://api.deepl.com/v2/translate',
                headers={'Authorization': f'DeepL-Auth-Key {DEEPL_API_KEY}'},
                data={'text': text, 'target_lang': target_lang},
                timeout=10
            )
            
            if resp.status_code == 200:
                translation = resp.json()['translations'][0]['text']
                
                # Cache the result
                self._cache_translation(text, target_lang, translation)
                
                return {
                    'success': True,
                    'translation': translation,
                    'cached': False,
                    'response_time': time.time() - start_time
                }
            else:
                return {
                    'success': False,
                    'error': f'DeepL API error: {resp.status_code}',
                    'response_time': time.time() - start_time
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': f'Translation error: {str(e)}',
                'response_time': time.time() - start_time
            }
    
    def translate_batch(self, texts: List[str], target_lang: str, request=None, api_key=None, endpoint_type='demo') -> List[Dict[str, any]]:
        """Translate multiple texts in batch, honoring IP rate limits for actual API calls only"""
        results = []
        
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            batch_results = []
            
            for text in batch:
                result = self.translate_single(text, target_lang, request=request, api_key=api_key, endpoint_type=endpoint_type)
                batch_results.append(result)
            
            results.extend(batch_results)
        
        return results

class SmartCacheOrchestrator:
    """Main orchestrator for the enhanced translation pipeline"""
    
    def __init__(self):
        self.priority_cache = PriorityCacheManager()
        self.batch_translator = TranslationBatcher()
        self.performance_metrics = defaultdict(list)
    
    def _identify_message_key(self, text: str) -> Optional[str]:
        """Identify if text matches a priority message"""
        text_lower = text.lower().strip()
        
        # Check critical messages
        critical_messages = self.priority_cache.get_critical_messages()
        for key, message in critical_messages.items():
            if text_lower == message.lower():
                return key
        
        # Check common responses
        common_responses = self.priority_cache.get_common_responses()
        for key, message in common_responses.items():
            if text_lower == message.lower():
                return key
        
        return None
    
    def handle_translation_request(self, text: str, target_lang: str, api_key: str, request=None, endpoint_type='demo') -> Dict[str, any]:
        """Main translation orchestration method (now supports passing request for IP DeepL call rate limit)"""
        start_time = time.time()
        
        # Check if this is a priority message
        message_key = self._identify_message_key(text)
        if message_key:
            priority_translation = self.priority_cache.get_cached_translation(message_key, target_lang)
            if priority_translation:
                response_time = time.time() - start_time
                self.performance_metrics['priority_cache_hits'].append(response_time)
                
                return {
                    'success': True,
                    'translation': priority_translation,
                    'cached': True,
                    'priority': True,
                    'response_time': response_time
                }
        
        # Paid or demo?
        if api_key and api_key != 'demo':
            call_endpoint_type = 'paid'
        else:
            call_endpoint_type = 'demo'

        # Fall back to regular translation, pass request for IP rate limiting
        result = self.batch_translator.translate_single(text, target_lang, request=request, api_key=api_key, endpoint_type=call_endpoint_type)
        
        # Log performance metrics
        if result.get('success'):
            cache_type = 'regular_cache_hit' if result['cached'] else 'api_translation'
            self.performance_metrics[cache_type].append(result['response_time'])
        
        return result
    
    def get_performance_metrics(self) -> Dict[str, any]:
        """Get performance metrics for monitoring"""
        metrics = {}
        
        for metric_type, times in self.performance_metrics.items():
            if times:
                metrics[metric_type] = {
                    'count': len(times),
                    'avg_time': sum(times) / len(times),
                    'min_time': min(times),
                    'max_time': max(times)
                }
        
        return metrics

# Initialize the orchestrator
cache_orchestrator = SmartCacheOrchestrator()

# ==== DATABASE INITIALIZATION ====

def init_db():
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    
    # Create users table with email verification fields
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    email_verified INTEGER DEFAULT 0,
                    verification_token TEXT,
                    verification_token_expires TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                  )''')
    
    # Create password_resets table
    c.execute('''CREATE TABLE IF NOT EXISTS password_resets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token TEXT NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    used INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                  )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    uses INTEGER DEFAULT 0
                  )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    subscription_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                  )''')
    
    conn.commit()
    conn.close()

# Initialize database and cache, and IP rate limiter table
init_db()
cache_orchestrator.priority_cache.init_priority_cache_db()
ip_rate_limiter.init_rate_limit_db()

# Initialize SES client with error handling
try:
    ses_client = boto3.client('ses', region_name=os.getenv('AWS_REGION'))
    print("✅ AWS SES client initialized successfully")
except Exception as e:
    print(f"⚠️  WARNING: AWS SES client failed to initialize: {e}")
    print("   Email features will be disabled")
    ses_client = None

# ==== ENHANCED API ROUTES ====

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/demo')
def demo():
    api_base = request.host_url.rstrip('/')
    return render_template(
        'live-demo.html',
        stripe_publishable_key=stripe_publishable_key,
        api_base_url=api_base
    )

@app.route('/live-demo')
def live_demo():
    api_base = request.host_url.rstrip('/')
    return render_template(
        'live-demo.html',
        stripe_publishable_key=stripe_publishable_key,
        api_base_url=api_base
    )

@app.route('/terms')
def terms():
    return render_template('terms-and-condition.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy-policy.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute('SELECT id, password_hash, email_verified FROM users WHERE email = ?', (email,))
        row = c.fetchone()
        conn.close()
        
        if row and check_password_hash(row[1], password):
            if row[2] == 1:  # email_verified
                session['user_id'] = row[0]
                session['user_email'] = email
                flash('Logged in successfully.', 'success')
                return redirect(url_for('profile'))
            else:
                flash('Please verify your email before logging in. Check your inbox for the verification link.', 'warning')
                return redirect(url_for('login'))
        else:
            flash('Invalid email or password.', 'error')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/profile')
def profile():
    user_id = session.get('user_id')
    user_email = session.get('user_email')
    if not user_id or not user_email:
        flash('Please log in to access your profile.', 'warning')
        return redirect(url_for('login'))
    
    # Check if user's email is verified
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT email_verified FROM users WHERE id = ?', (user_id,))
    row = c.fetchone()
    if not row or row[0] != 1:
        flash('Please verify your email before accessing your profile.', 'warning')
        return redirect(url_for('login'))
    
    # Check if user has any active subscriptions
    c.execute('SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND status = "active"', (user_id,))
    active_subscriptions = c.fetchone()[0]
    
    conn.close()
    
    # If no active subscription, show the "need to subscribe" message
    if active_subscriptions == 0:
        return render_template('profile.html', 
                             current_user={'username': user_email}, 
                             key=None,
                             subscription_status='inactive')
    
    # If they have active subscription, generate API key
    # (This code will run after they complete Stripe checkout)
    key = session.get('api_key')
    if not key:
        key = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute('INSERT INTO api_keys (key, created) VALUES (?, ?)', (key, now))
        conn.commit()
        conn.close()
        session['api_key'] = key
    
    # Get API key usage info
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT created, uses FROM api_keys WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    
    return render_template('profile.html', 
                         current_user={'username': user_email}, 
                         key=row and (key, row[0], row[1]),
                         subscription_status='active')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        agree_terms = request.form.get('agreeTerms')
        
        if not email or not password:
            flash('Email and password are required.')
            return redirect(url_for('register'))
        
        if password != confirm_password:
            flash('Passwords do not match.')
            return redirect(url_for('register'))
        
        if not agree_terms:
            flash('You must agree to the Terms of Service and Privacy Policy.')
            return redirect(url_for('register'))
        
        verification_token = uuid.uuid4().hex
        verification_token_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        pw_hash = generate_password_hash(password)
        
        try:
            conn = sqlite3.connect('api_keys.db')
            c = conn.cursor()
            c.execute('INSERT INTO users (full_name, email, password_hash, verification_token, verification_token_expires) VALUES (?, ?, ?, ?, ?)', (full_name, email, pw_hash, verification_token, verification_token_expires))
            conn.commit()
            user_id = c.lastrowid
            conn.close()
            
            # Send verification email
            verification_url = f"{request.host_url.rstrip('/')}/verify-email?token={verification_token}"
            
            try:
                if ses_client:
                    ses_client.send_email(
                        Source=os.getenv('SES_EMAIL', 'noreply@argonautdigitalventures.com'),
                        Destination={'ToAddresses': [email]},
                        Message={
                            'Subject': {'Data': 'Verify Your Email - Argonaut Digital Ventures'},
                            'Body': {
                                'Html': {
                                    'Data': f'''
                                    <html>
                                    <head>
                                        <style>
                                            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                                            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                                            .header {{ background: #dc2626; color: white; padding: 20px; text-align: center; }}
                                            .content {{ background: #f8f9fa; padding: 30px; }}
                                            .button {{ display: inline-block; background: #dc2626; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; }}
                                            .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #666; }}
                                        </style>
                                    </head>
                                    <body>
                                        <div class="container">
                                            <div class="header">
                                                <h1>Welcome to Argonaut Digital Ventures!</h1>
                                            </div>
                                            <div class="content">
                                                <h2>Hi {full_name or 'there'}!</h2>
                                                <p>Thank you for registering with our TranslateAll API platform. To complete your registration and access your account, please verify your email address.</p>
                                                <p style="text-align: center; margin: 30px 0;">
                                                    <a href="{verification_url}" class="button">Verify Your Email</a>
                                                </p>
                                                <p>Or copy and paste this link into your browser:</p>
                                                <p style="word-break: break-all; color: #666;">{verification_url}</p>
                                                <p><strong>This link will expire in 30 minutes.</strong></p>
                                                <p>If you didn't create this account, please ignore this email.</p>
                                            </div>
                                            <div class="footer">
                                                <p> 2025 Argonaut Digital Ventures. All rights reserved.</p>
                                            </div>
                                        </div>
                                    </body>
                                    </html>
                                    '''
                                }
                            }
                        }
                    )
                    flash('Registration successful! Please check your email and click the verification link to complete your account setup.', 'success')
                else:
                    # If SES is not configured, auto-verify for development
                    conn = sqlite3.connect('api_keys.db')
                    c = conn.cursor()
                    c.execute('UPDATE users SET email_verified = 1 WHERE id = ?', (user_id,))
                    conn.commit()
                    conn.close()
                    session['user_id'] = user_id
                    session['user_email'] = email
                    flash('Registration successful! (Email verification skipped in development mode)', 'success')
                    return redirect(url_for('profile'))
                    
            except ClientError as e:
                print(f"SES send failed: {e}")
                flash('Registration successful, but we had trouble sending the verification email. Please contact support.', 'warning')
            
            return redirect(url_for('login'))
            
        except sqlite3.IntegrityError:
            flash('Email already registered.')
            return redirect(url_for('register'))
        
    return render_template('register.html')

@app.route('/health')
def health():
    return jsonify(status='healthy', service='Enhanced TranslateAll API')

@app.route('/create-key', methods=['POST'])
def create_key():
    new_key = uuid.uuid4().hex
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('INSERT INTO api_keys (key, created) VALUES (?, ?)',
              (new_key, datetime.now(timezone.utc)))
    conn.commit()
    conn.close()
    return jsonify(apiKey=new_key)

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify(error='Authentication required'), 401
    
    # Check if user's email is verified
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT email_verified FROM users WHERE id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    
    if not row or row[0] != 1:
        return jsonify(error='Please verify your email before making a purchase'), 403
    
    user_email = session.get('user_email')
    api_key = session.get('api_key')
    
    ck = stripe.checkout.Session.create(
        line_items=[{"price": stripe_price_id, "quantity": 1}],
        mode='subscription',
        success_url=request.host_url + '?success=true',
        cancel_url=request.host_url + '?canceled=true',
        customer_email=user_email,
        metadata={'user_id': user_id, 'api_key': api_key}
    )
    return jsonify(sessionId=ck.id)

# ==== ENHANCED TRANSLATION ENDPOINTS ====

@app.route('/translate', methods=['POST'])
def translate():
    """Enhanced translation endpoint with smart caching and IP DeepL call rate limiting"""
    key = request.headers.get('X-API-KEY')
    if not key:
        return jsonify(success=False, error='API key required'), 401
    
    # Check if DeepL API key is configured
    if not DEEPL_API_KEY:
        return jsonify(success=False, error='Translation service not configured. Please contact administrator.'), 503
    
    # Validate API key and quota
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT uses FROM api_keys WHERE key=?', (key,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(success=False, error='Invalid API key'), 401
    
    uses = row[0] + 1
    if uses > 2000:
        conn.close()
        return jsonify(success=False, error='Quota exceeded'), 403
    
    c.execute('UPDATE api_keys SET uses=? WHERE key=?', (uses, key))
    conn.commit()
    conn.close()

    body = request.get_json()
    text = (body or {}).get('text', '').strip()
    target_lang = body.get('target', 'ES')
    
    if not text:
        return jsonify(success=False, error='No text provided')
    
    # Smart DeepL API call rate limiting by IP for PAID (generous) or DEMO (strict)
    
    # Call the orchestrator, which delegates to our DeepL call limiting logic for uncached
    try:
        result = cache_orchestrator.handle_translation_request(
            text, target_lang, key, request=request,
            endpoint_type='paid' if key != 'demo' else 'demo'
        )
        
        # If translation failed due to API issues or explicit IP limit, catch that:
        if not result.get('success'):
            error_msg = result.get('error', 'Translation failed')
            if error_msg and "DeepL API calls from your IP" in error_msg:
                # IP-based DeepL call rate limit exceeded
                return jsonify(success=False, error=error_msg, ip_rate_limit=result.get('ip_rate_limit')), 429
            if 'DeepL API error: 403' in error_msg:
                return jsonify(success=False, error='DeepL API error: 403 - Invalid API key or quota exceeded'), 403
            elif 'DeepL API error: 456' in error_msg:
                return jsonify(success=False, error='DeepL quota exceeded'), 429
            else:
                return jsonify(success=False, error=error_msg), 500
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify(success=False, error=f'Translation service error: {str(e)}'), 500

@app.route('/translate-batch', methods=['POST'])
def translate_batch():
    """Batch translation endpoint (with smart IP DeepL call rate limiting for DeepL, only uncached are counted)"""
    key = request.headers.get('X-API-KEY')
    if not key:
        return jsonify(success=False, error='API key required'), 401
    
    # Validate API key
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT uses FROM api_keys WHERE key=?', (key,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(success=False, error='Invalid API key'), 401
    
    body = request.get_json()
    texts = body.get('texts', [])
    target_lang = body.get('target', 'ES')
    
    if not texts or not isinstance(texts, list):
        return jsonify(success=False, error='No texts provided')
    
    if len(texts) > 50:
        return jsonify(success=False, error='Too many texts (max 50)')
    
    # Check quota for batch
    uses = row[0] + len(texts)
    if uses > 2000:
        conn.close()
        return jsonify(success=False, error='Quota would be exceeded'), 403
    
    c.execute('UPDATE api_keys SET uses=? WHERE key=?', (uses, key))
    conn.commit()
    conn.close()
    
    # batch_translate applies smart DeepL IP call limiting ONLY for uncached requests
    try:
        call_endpoint_type = 'paid' if key != 'demo' else 'demo'
        results = cache_orchestrator.batch_translator.translate_batch(
            texts, target_lang, request=request, api_key=key, endpoint_type=call_endpoint_type
        )
        # Any IP-based limit blocking gets returned in the individual result(s)
        return jsonify(success=True, results=results)
        
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/cache-populate', methods=['POST'])
def populate_cache():
    """Populate priority cache for a language"""
    key = request.headers.get('X-API-KEY')
    if not key:
        return jsonify(success=False, error='API key required'), 401
    
    # Validate API key
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT uses FROM api_keys WHERE key=?', (key,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(success=False, error='Invalid API key'), 401
    conn.close()
    
    body = request.get_json()
    target_lang = body.get('target_lang', 'ES')
    
    # Start background cache population
    status = cache_orchestrator.priority_cache.populate_priority_cache(target_lang)
    
    return jsonify(
        success=True, 
        message=f'Cache population {status} for {target_lang}',
        status=status
    )

@app.route('/cache-status', methods=['GET'])
def cache_status():
    """Get cache status for debugging"""
    key = request.headers.get('X-API-KEY')
    if not key:
        return jsonify(success=False, error='API key required'), 401
    
    # Validate API key
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT uses FROM api_keys WHERE key=?', (key,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(success=False, error='Invalid API key'), 401
    conn.close()
    
    target_lang = request.args.get('lang', 'ES')
    
    cache_info = cache_orchestrator.priority_cache.get_cache_status(target_lang)
    return jsonify(cache_info)

@app.route('/performance-metrics', methods=['GET'])
def performance_metrics():
    """Get performance metrics"""
    key = request.headers.get('X-API-KEY')
    if not key:
        return jsonify(success=False, error='API key required'), 401
    
    # Validate API key
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT uses FROM api_keys WHERE key=?', (key,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify(success=False, error='Invalid API key'), 401
    conn.close()
    
    metrics = cache_orchestrator.get_performance_metrics()
    return jsonify(metrics)

# ==== EXISTING ROUTES ====

@app.route('/resend-verification', methods=['POST'])
def resend_verification():
    data = request.get_json() or {}
    email = data.get('email', '').lower().strip()
    
    if not email:
        return jsonify(success=False, error='Email is required')
    
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT id, full_name, email_verified FROM users WHERE email = ?', (email,))
    row = c.fetchone()
    
    if not row:
        return jsonify(success=False, error='Email not found')
    
    user_id, full_name, email_verified = row
    
    if email_verified == 1:
        return jsonify(success=False, error='Email is already verified')
    
    # Generate new verification token
    verification_token = uuid.uuid4().hex
    verification_token_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    
    c.execute('UPDATE users SET verification_token = ?, verification_token_expires = ? WHERE id = ?', 
              (verification_token, verification_token_expires, user_id))
    conn.commit()
    conn.close()
    
    # Send verification email
    verification_url = f"{request.host_url.rstrip('/')}/verify-email?token={verification_token}"
    
    try:
        if ses_client:
            ses_client.send_email(
                Source=os.getenv('SES_EMAIL', 'noreply@argonautdigitalventures.com'),
                Destination={'ToAddresses': [email]},
                Message={
                    'Subject': {'Data': 'Verify Your Email - Argonaut Digital Ventures'},
                    'Body': {
                        'Html': {
                            'Data': f'''
                            <html>
                            <head>
                                <style>
                                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                                    .header {{ background: #dc2626; color: white; padding: 20px; text-align: center; }}
                                    .content {{ background: #f8f9fa; padding: 30px; }}
                                    .button {{ display: inline-block; background: #dc2626; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; }}
                                    .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #666; }}
                                </style>
                            </head>
                            <body>
                                <div class="container">
                                    <div class="header">
                                        <h1>Email Verification</h1>
                                    </div>
                                    <div class="content">
                                        <h2>Hi {full_name or 'there'}!</h2>
                                        <p>Here's your new verification link to complete your registration:</p>
                                        <p style="text-align: center; margin: 30px 0;">
                                            <a href="{verification_url}" class="button">Verify Your Email</a>
                                        </p>
                                        <p>Or copy and paste this link into your browser:</p>
                                        <p style="word-break: break-all; color: #666;">{verification_url}</p>
                                        <p><strong>This link will expire in 30 minutes.</strong></p>
                                    </div>
                                    <div class="footer">
                                        <p> 2025 Argonaut Digital Ventures. All rights reserved.</p>
                                    </div>
                                </div>
                            </body>
                            </html>
                            '''
                        }
                    }
                }
            )
            return jsonify(success=True, message='Verification email sent successfully')
        else:
            return jsonify(success=False, error='Email service not configured')
            
    except ClientError as e:
        print(f"SES send failed: {e}")
        return jsonify(success=False, error='Failed to send verification email')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        
        if not email:
            flash('Email is required.', 'error')
            return redirect(url_for('forgot_password'))
        
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute('SELECT id, full_name FROM users WHERE email = ?', (email,))
        row = c.fetchone()
        
        if not row:
            # Don't reveal if email exists or not for security
            flash('If an account with that email exists, we have sent you a password reset link.', 'success')
            return redirect(url_for('forgot_password'))
        
        user_id, full_name = row
        
        # Generate password reset token
        reset_token = uuid.uuid4().hex
        reset_token_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        
        c.execute('INSERT INTO password_resets (user_id, token, expires_at) VALUES (?,?,?)',
                  (user_id, reset_token, reset_token_expires))
        conn.commit()
        conn.close()
        
        # Send password reset email
        reset_url = f"{request.host_url.rstrip('/')}/reset-password?token={reset_token}"
        
        try:
            if ses_client:
                ses_client.send_email(
                    Source=os.getenv('SES_EMAIL', 'noreply@argonautdigitalventures.com'),
                    Destination={'ToAddresses': [email]},
                    Message={
                        'Subject': {'Data': 'Password Reset - Argonaut Digital Ventures'},
                        'Body': {
                            'Html': {
                                'Data': f'''
                                <html>
                                <head>
                                    <style>
                                        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                                        .header {{ background: #dc2626; color: white; padding: 20px; text-align: center; }}
                                        .content {{ background: #f8f9fa; padding: 30px; }}
                                        .button {{ display: inline-block; background: #dc2626; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; }}
                                        .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #666; }}
                                    </style>
                                </head>
                                <body>
                                    <div class="container">
                                        <div class="header">
                                            <h1>Password Reset Request</h1>
                                        </div>
                                        <div class="content">
                                            <h2>Hi {full_name or 'there'}!</h2>
                                            <p>You have requested to reset your password for your Argonaut Digital Ventures account.</p>
                                            <p>Click the button below to reset your password:</p>
                                            <p style="text-align: center; margin: 30px 0;">
                                                <a href="{reset_url}" class="button">Reset Password</a>
                                            </p>
                                            <p>Or copy and paste this link into your browser:</p>
                                            <p style="word-break: break-all; color: #666;">{reset_url}</p>
                                            <p><strong>This link will expire in 30 minutes.</strong></p>
                                            <p>If you did not request a password reset, please ignore this email.</p>
                                        </div>
                                        <div class="footer">
                                            <p> 2025 Argonaut Digital Ventures. All rights reserved.</p>
                                        </div>
                                    </div>
                                </body>
                                </html>
                                '''
                            }
                        }
                    }
                )
                flash('If an account with that email exists, we have sent you a password reset link.', 'success')
            else:
                flash('Email service not configured. Please contact support.', 'error')
                
        except ClientError as e:
            print(f"SES send failed: {e}")
            flash('Failed to send password reset email. Please try again.', 'error')
        
        return redirect(url_for('forgot_password'))
    
    return render_template('forgot_password.html')

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    token = request.args.get('token') or request.form.get('token')
    
    if not token:
        flash('Invalid or missing reset token.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if not password or not confirm_password:
            flash('Both password fields are required.', 'error')
            return render_template('reset_password.html', token=token)
        
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token)
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template('reset_password.html', token=token)
        
        # Verify token and update password
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute('SELECT user_id, expires_at, used FROM password_resets WHERE token = ?', (token,))
        row = c.fetchone()
        
        if not row:
            flash('Invalid reset token.', 'error')
            return redirect(url_for('login'))
        
        user_id, expires_at, used = row
        
        if used == 1:
            flash('Reset token has already been used.', 'error')
            return redirect(url_for('forgot_password'))
        
        if expires_at < datetime.now(timezone.utc):
            flash('Reset token has expired. Please request a new password reset.', 'error')
            return redirect(url_for('forgot_password'))
        
        # Update password and mark token as used
        password_hash = generate_password_hash(password)
        c.execute('UPDATE users SET password_hash = ? WHERE id = ?', 
                  (password_hash, user_id))
        c.execute('UPDATE password_resets SET used = 1 WHERE token = ?', (token,))
        conn.commit()
        conn.close()
        
        flash('Password updated successfully! You can now sign in with your new password.', 'success')
        return redirect(url_for('login'))
    
    # GET request - verify token and show reset form
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT user_id, expires_at, used FROM password_resets WHERE token = ?', (token,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        flash('Invalid reset token.', 'error')
        return redirect(url_for('login'))
    
    user_id, expires_at, used = row
    
    if used == 1:
        flash('Reset token has already been used.', 'error')
        return redirect(url_for('forgot_password'))
    
    if expires_at < datetime.now(timezone.utc):
        flash('Reset token has expired. Please request a new password reset.', 'error')
        return redirect(url_for('forgot_password'))
    
    return render_template('reset_password.html', token=token)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('user_email', None)
    session.pop('api_key', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        print('Webhook signature verification failed:', e)
        return '', 400
    
    if event['type'] == 'checkout.session.completed':
        sess = event['data']['object']
        uid = int(sess['metadata'].get('user_id', 0))
        cust = sess.get('customer')
        sub_id = sess.get('subscription')
        
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute(('''INSERT OR REPLACE INTO subscriptions
                     (user_id, customer_id, subscription_id, status)
                     VALUES (?, ?, ?, ?)'''),
                  (uid, cust, sub_id, 'active'))
        conn.commit()
        conn.close()
        
    elif event['type'] == 'customer.subscription.deleted':
        sub = event['data']['object']
        sub_id = sub.get('id')
        
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute('UPDATE subscriptions SET status = ? WHERE subscription_id = ?', ('canceled', sub_id))
        
        key_to_revoke = sub['metadata'].get('api_key')
        if key_to_revoke:
            c.execute('DELETE FROM api_keys WHERE key = ?', (key_to_revoke,))
        conn.commit()
        conn.close()
    
    return '', 200

@app.route('/demo-translate', methods=['POST'])
def demo_translate():
    """Demo translation endpoint (NO API KEY, but strict IP DeepL call limiting, only for uncached)"""
    body = request.get_json()
    text = (body or {}).get('text', '').strip()
    target_lang = body.get('target', 'ES')
    
    if not text:
        return jsonify(success=False, error='No text provided')
    
    # Check if DeepL API key is configured
    if not DEEPL_API_KEY:
        return jsonify(success=False, error='Translation service not configured')
    
    # Use smart cache orchestrator (no API key needed for demo)... pass request object so DeepL call limiting by IP can function
    try:
        result = cache_orchestrator.handle_translation_request(
            text, target_lang, 'demo', request=request, endpoint_type='demo'
        )
        if not result.get('success'):
            error_msg = result.get('error', 'Translation failed')
            if error_msg and "DeepL API calls from your IP" in error_msg:
                return jsonify(success=False, error=error_msg, ip_rate_limit=result.get('ip_rate_limit')), 429
        return jsonify(result)
        
    except Exception as e:
        return jsonify(success=False, error=f'Translation error: {str(e)}'), 500

@app.route('/rate-limit-status', methods=['GET'])
def rate_limit_status():
    """Check IP rate limit status without making an actual translation request"""
    endpoint_type = request.args.get('type', 'demo')  # 'demo' or 'paid'
    
    # Validate endpoint type
    if endpoint_type not in ['demo', 'paid']:
        return jsonify(success=False, error='Invalid endpoint type. Use "demo" or "paid"'), 400
    
    try:
        status = ip_rate_limiter.get_rate_limit_info(request, endpoint_type)
        return jsonify({
            'success': True,
            'ip_address': ip_rate_limiter.get_client_ip(request),
            'endpoint_type': endpoint_type,
            'status': status
        })
    except Exception as e:
        return jsonify(success=False, error=f'Error checking rate limit: {str(e)}'), 500

@app.route('/verify-email', methods=['GET'])
def verify_email():
    token = request.args.get('token')
    if not token:
        flash('Invalid verification link.')
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT id, verification_token_expires FROM users WHERE verification_token = ?', (token,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        flash('Invalid verification link.')
        return redirect(url_for('login'))
    
    user_id, expires_at = row
    if expires_at < datetime.now(timezone.utc):
        flash('Verification link has expired.')
        return redirect(url_for('login'))
    
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('UPDATE users SET email_verified = 1, verification_token = NULL, verification_token_expires = NULL WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    flash('Email verified successfully! You can now log in.', 'success')
    return redirect(url_for('login'))

@app.route('/resend-verification', methods=['POST'])
def resend_verification():
    data = request.get_json() or {}
    email = data.get('email', '').lower().strip()
    
    if not email:
        return jsonify(success=False, error='Email is required')
    
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT id, full_name, email_verified FROM users WHERE email = ?', (email,))
    row = c.fetchone()
    
    if not row:
        return jsonify(success=False, error='Email not found')
    
    user_id, full_name, email_verified = row
    
    if email_verified == 1:
        return jsonify(success=False, error='Email is already verified')
    
    # Generate new verification token
    verification_token = uuid.uuid4().hex
    verification_token_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    
    c.execute('UPDATE users SET verification_token = ?, verification_token_expires = ? WHERE id = ?', 
              (verification_token, verification_token_expires, user_id))
    conn.commit()
    conn.close()
    
    # Send verification email
    verification_url = f"{request.host_url.rstrip('/')}/verify-email?token={verification_token}"
    
    try:
        if ses_client:
            ses_client.send_email(
                Source=os.getenv('SES_EMAIL', 'noreply@argonautdigitalventures.com'),
                Destination={'ToAddresses': [email]},
                Message={
                    'Subject': {'Data': 'Verify Your Email - Argonaut Digital Ventures'},
                    'Body': {
                        'Html': {
                            'Data': f'''
                            <html>
                            <head>
                                <style>
                                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                                    .header {{ background: #dc2626; color: white; padding: 20px; text-align: center; }}
                                    .content {{ background: #f8f9fa; padding: 30px; }}
                                    .button {{ display: inline-block; background: #dc2626; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; }}
                                    .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #666; }}
                                </style>
                            </head>
                            <body>
                                <div class="container">
                                    <div class="header">
                                        <h1>Email Verification</h1>
                                    </div>
                                    <div class="content">
                                        <h2>Hi {full_name or 'there'}!</h2>
                                        <p>Here's your new verification link to complete your registration:</p>
                                        <p style="text-align: center; margin: 30px 0;">
                                            <a href="{verification_url}" class="button">Verify Your Email</a>
                                        </p>
                                        <p>Or copy and paste this link into your browser:</p>
                                        <p style="word-break: break-all; color: #666;">{verification_url}</p>
                                        <p><strong>This link will expire in 30 minutes.</strong></p>
                                    </div>
                                    <div class="footer">
                                        <p> 2025 Argonaut Digital Ventures. All rights reserved.</p>
                                    </div>
                                </div>
                            </body>
                            </html>
                            '''
                        }
                    }
                }
            )
            return jsonify(success=True, message='Verification email sent successfully')
        else:
            return jsonify(success=False, error='Email service not configured')
            
    except ClientError as e:
        print(f"SES send failed: {e}")
        return jsonify(success=False, error='Failed to send verification email')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)), debug=True)
