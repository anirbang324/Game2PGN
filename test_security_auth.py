import sqlite3
import time
import json
import secrets
import hashlib
import bcrypt
import pyotp

DB_PATH = ":memory:"  # In-memory DB for test

def init_db(conn):
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            totp_secret TEXT,
            totp_enabled INTEGER DEFAULT 0,
            backup_codes_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_notations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            event TEXT,
            date TEXT,
            white_player TEXT,
            black_player TEXT,
            result TEXT,
            pgn_text TEXT NOT NULL,
            csv_text TEXT NOT NULL,
            game_data_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            identifier TEXT PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            last_failed REAL DEFAULT 0
        )
    """)
    conn.commit()

# --- Password Helpers ---
def validate_password_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit."
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        return False, "Password must contain at least one special character."
    return True, "OK"

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False

# --- Rate Limiting ---
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_TIME_SECONDS = 900  # 15 mins

def check_login_lockout(conn, identifier: str) -> tuple[bool, str]:
    cursor = conn.cursor()
    cursor.execute("SELECT attempts, last_failed FROM login_attempts WHERE identifier = ?", (identifier.lower(),))
    row = cursor.fetchone()
    if row:
        attempts, last_failed = row
        if attempts >= MAX_LOGIN_ATTEMPTS:
            remaining = LOCKOUT_TIME_SECONDS - (time.time() - last_failed)
            if remaining > 0:
                mins = int(remaining // 60) + 1
                return True, f"Account locked due to multiple failed login attempts. Try again in {mins} minutes."
            else:
                # Reset after lockout period expires
                cursor.execute("DELETE FROM login_attempts WHERE identifier = ?", (identifier.lower(),))
                conn.commit()
    return False, ""

def record_failed_login(conn, identifier: str):
    cursor = conn.cursor()
    cursor.execute("SELECT attempts FROM login_attempts WHERE identifier = ?", (identifier.lower(),))
    row = cursor.fetchone()
    now = time.time()
    if row:
        cursor.execute("UPDATE login_attempts SET attempts = attempts + 1, last_failed = ? WHERE identifier = ?", (now, identifier.lower()))
    else:
        cursor.execute("INSERT INTO login_attempts (identifier, attempts, last_failed) VALUES (?, 1, ?)", (identifier.lower(), now))
    conn.commit()
    time.sleep(0.1)  # Artificial delay for anti-timing protection

def clear_failed_logins(conn, identifier: str):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM login_attempts WHERE identifier = ?", (identifier.lower(),))
    conn.commit()

# --- Backup Code Helpers ---
def hash_backup_code(code: str) -> str:
    cleaned = code.strip().replace("-", "").upper()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()

def generate_backup_codes(count: int = 8) -> tuple[list[str], list[str]]:
    raw_codes = []
    hashed_codes = []
    for _ in range(count):
        token = secrets.token_hex(4).upper()
        formatted = f"{token[:4]}-{token[4:]}"
        raw_codes.append(formatted)
        hashed_codes.append(hash_backup_code(formatted))
    return raw_codes, hashed_codes

def verify_and_consume_backup_code(conn, user_id: int, input_code: str) -> bool:
    cursor = conn.cursor()
    cursor.execute("SELECT backup_codes_json FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        return False
    hashed_list = json.loads(row[0])
    target_hash = hash_backup_code(input_code)
    if target_hash in hashed_list:
        hashed_list.remove(target_hash)
        cursor.execute("UPDATE users SET backup_codes_json = ? WHERE id = ?", (json.dumps(hashed_list), user_id))
        conn.commit()
        return True
    return False

# --- User Auth Tests ---
def run_tests():
    conn = sqlite3.connect(":memory:")
    init_db(conn)

    print("--- Test 1: Password Strength Validation ---")
    valid, msg = validate_password_strength("weak")
    assert not valid
    valid, msg = validate_password_strength("StrongP@ss1")
    assert valid
    print("✅ Password strength rules working correctly.")

    print("\n--- Test 2: User Registration & Password Hashing ---")
    pwd_hash = hash_password("StrongP@ss1")
    assert verify_password("StrongP@ss1", pwd_hash)
    assert not verify_password("WrongP@ss1", pwd_hash)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ("testuser", pwd_hash))
    conn.commit()
    user_id = cursor.lastrowid
    print(f"✅ User registered with ID {user_id} and bcrypt hashed password.")

    print("\n--- Test 3: Rate Limiting & Lockout ---")
    for i in range(5):
        locked, _ = check_login_lockout(conn, "testuser")
        assert not locked
        record_failed_login(conn, "testuser")
    locked, msg = check_login_lockout(conn, "testuser")
    assert locked
    print(f"✅ Account locked after 5 failed attempts: '{msg}'")

    clear_failed_logins(conn, "testuser")
    locked, _ = check_login_lockout(conn, "testuser")
    assert not locked
    print("✅ Lockout cleared upon successful login.")

    print("\n--- Test 4: 2FA TOTP Generation & Verification ---")
    totp_secret = pyotp.random_base32()
    totp = pyotp.TOTP(totp_secret)
    current_code = totp.now()
    assert totp.verify(current_code)
    assert not totp.verify("000000")
    print("✅ TOTP setup and verification working.")

    print("\n--- Test 5: Backup Codes (Hashed & Single-Use) ---")
    raw_codes, hashed_codes = generate_backup_codes(8)
    cursor.execute("UPDATE users SET backup_codes_json = ? WHERE id = ?", (json.dumps(hashed_codes), user_id))
    conn.commit()

    test_code = raw_codes[0]
    # Verify first use succeeds
    assert verify_and_consume_backup_code(conn, user_id, test_code)
    # Verify second use fails (single use)
    assert not verify_and_consume_backup_code(conn, user_id, test_code)
    print("✅ Backup codes properly hashed and single-use validated.")

    print("\n--- Test 6: SQL Injection Protection & IDOR Prevention ---")
    # Register user 2
    cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ("user2", hash_password("OtherP@ss1")))
    user2_id = cursor.lastrowid

    # Add notation for user 1
    cursor.execute("""
        INSERT INTO saved_notations (user_id, title, pgn_text, csv_text, game_data_json)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, "User 1 Game", "1. e4 e5", "1,e4,e5", '{"moves":["e4","e5"]}'))
    conn.commit()
    notation_id = cursor.lastrowid

    # Try accessing notation as User 2 (IDOR check)
    cursor.execute("SELECT * FROM saved_notations WHERE id = ? AND user_id = ?", (notation_id, user2_id))
    assert cursor.fetchone() is None

    # Access notation as User 1
    cursor.execute("SELECT * FROM saved_notations WHERE id = ? AND user_id = ?", (notation_id, user_id))
    assert cursor.fetchone() is not None

    # Test SQL injection attack payload string
    malicious_input = "1' OR '1'='1"
    cursor.execute("SELECT * FROM users WHERE username = ?", (malicious_input,))
    assert cursor.fetchone() is None
    print("✅ SQL Injection prevented and IDOR authorization check verified.")

    print("\n🎉 ALL SECURITY & DATABASE TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
