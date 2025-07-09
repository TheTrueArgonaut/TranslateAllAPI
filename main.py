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

@app.context_processor
def inject_now():
    return {'now': datetime.now(timezone.utc)}

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
    """Handles batch translation processing with rate limiting"""
    
    def __init__(self, rate_limit: int = TRANSLATION_RATE_LIMIT):
        self.rate_limit = rate_limit
        self.batch_size = BATCH_SIZE
        self.last_request_time = 0
        self.request_count = 0
        self.rate_lock = threading.Lock()
    
    def _rate_limit_check(self):
        """Implement rate limiting"""
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
    
    def translate_single(self, text: str, target_lang: str) -> Dict[str, any]:
        """Translate single text with caching and rate limiting"""
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
        
        # Apply rate limiting
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
    
    def translate_batch(self, texts: List[str], target_lang: str) -> List[Dict[str, any]]:
        """Translate multiple texts in batch"""
        results = []
        
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            batch_results = []
            
            for text in batch:
                result = self.translate_single(text, target_lang)
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
    
    def handle_translation_request(self, text: str, target_lang: str, api_key: str) -> Dict[str, any]:
        """Main translation orchestration method"""
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
        
        # Fall back to regular translation
        result = self.batch_translator.translate_single(text, target_lang)
        
        # Log performance metrics
        if result['success']:
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
    
    # Existing tables
    c.execute('''CREATE TABLE IF NOT EXISTS api_keys (
                  key TEXT PRIMARY KEY, created TIMESTAMP, uses INTEGER DEFAULT 0
                  )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS sample_uses (
                  ip TEXT PRIMARY KEY, uses INTEGER DEFAULT 0
                  )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                  )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS password_resets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         user_id INTEGER NOT NULL,
         customer_id TEXT NOT NULL,
         subscription_id TEXT NOT NULL,
         status TEXT NOT NULL,
         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
     )''')
    
    conn.commit()
    conn.close()

# Initialize database and cache
init_db()
cache_orchestrator.priority_cache.init_priority_cache_db()

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
        'demo.html',
        stripe_publishable_key=stripe_publishable_key,
        api_base_url=api_base
    )

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        conn = sqlite3.connect('api_keys.db')
        c = conn.cursor()
        c.execute('SELECT id, password_hash FROM users WHERE email = ?', (email,))
        row = c.fetchone()
        conn.close()
        if row and check_password_hash(row[1], password):
            session['user_id'] = row[0]
            session['user_email'] = email
            flash('Logged in successfully.', 'success')
            return redirect(url_for('profile'))
        flash('Invalid email or password.')
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/profile')
def profile():
    user_id = session.get('user_id')
    user_email = session.get('user_email')
    if not user_id or not user_email:
        flash('Please log in to access your profile.', 'warning')
        return redirect(url_for('login'))
    
    # For now, since there are no active subscriptions, always show inactive status
    # This will be updated when Stripe webhook creates subscriptions
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    
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
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        if not email or not password:
            flash('Email and password are required.')
            return redirect(url_for('register'))
        pw_hash = generate_password_hash(password)
        try:
            conn = sqlite3.connect('api_keys.db')
            c = conn.cursor()
            c.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', (email, pw_hash))
            conn.commit()
            session['user_id'] = c.lastrowid
            session['user_email'] = email
            flash('Registration successful. You are now logged in.', 'success')
            return redirect(url_for('profile'))
        except sqlite3.IntegrityError:
            flash('Email already registered.')
            return redirect(url_for('register'))
        finally:
            conn.close()
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
    """Enhanced translation endpoint with smart caching"""
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
    
    # Use smart cache orchestrator
    try:
        result = cache_orchestrator.handle_translation_request(text, target_lang, key)
        
        # If translation failed due to API issues, return specific error
        if not result.get('success'):
            error_msg = result.get('error', 'Translation failed')
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
    """Batch translation endpoint"""
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
    
    try:
        results = cache_orchestrator.batch_translator.translate_batch(texts, target_lang)
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

@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json() or {}
    email = data.get('email','').lower().strip()
    conn = sqlite3.connect('api_keys.db')
    c = conn.cursor()
    c.execute('SELECT id FROM users WHERE email=?', (email,))
    row = c.fetchone()
    if row:
        user_id = row[0]
        token = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        c.execute('INSERT INTO password_resets (user_id, token, expires_at) VALUES (?,?,?)',
                  (user_id, token, expires))
        conn.commit()
        reset_url = f"{request.host_url.rstrip('/')}/reset-password?token={token}"
        try:
            if ses_client:
                ses_client.send_email(
                    Source=os.getenv('SES_EMAIL'),
                    Destination={'ToAddresses':[email]},
                    Message={
                        'Subject': {'Data':'Reset Your Password'},
                        'Body': {'Text': {'Data':f'Click to reset your password: {reset_url}'}}
                    }
                )
        except ClientError as e:
            app.logger.error(f"SES send failed: {e}")
    conn.close()
    return jsonify(success=True)

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
    """Demo translation endpoint that doesn't require API key"""
    body = request.get_json()
    text = (body or {}).get('text', '').strip()
    target_lang = body.get('target', 'ES')
    
    if not text:
        return jsonify(success=False, error='No text provided')
    
    # Check if DeepL API key is configured
    if not DEEPL_API_KEY:
        return jsonify(success=False, error='Translation service not configured')
    
    # Use smart cache orchestrator (no API key needed for demo)
    try:
        result = cache_orchestrator.handle_translation_request(text, target_lang, 'demo')
        return jsonify(result)
        
    except Exception as e:
        return jsonify(success=False, error=f'Translation error: {str(e)}'), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)), debug=True)
