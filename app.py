import re
import time
import streamlit as st
import google.genai as genai
from google.genai import types
import chess
import chess.pgn
import csv
import io
import json
import sqlite3
import secrets
import hashlib
import bcrypt
import pyotp
import qrcode
from PIL import Image
from datetime import date

# ─────────────────────────────────────────────
# Database & Security Core
# ─────────────────────────────────────────────

import os as _os
DB_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "chess_app.db")
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_TIME_SECONDS = 900  # 15 minutes lockout

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            security_question TEXT,
            security_answer_hash TEXT,
            totp_secret TEXT,
            totp_enabled INTEGER DEFAULT 0,
            backup_codes_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Saved notations table
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
    # Failed login attempts tracking (rate limiting / brute-force protection)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            identifier TEXT PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            last_failed REAL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

# Initialize DB on load
init_db()

# --- Security Helpers ---

def validate_username(username: str) -> tuple[bool, str]:
    username = username.strip()
    if len(username) < 3 or len(username) > 30:
        return False, "Username must be between 3 and 30 characters."
    if not re.match(r"^[a-zA-Z0-9_.\-]+$", username):
        return False, "Username can only contain letters, numbers, underscores, periods, and hyphens."
    return True, "OK"

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
        return False, "Password must contain at least one special character (!@#$%^&*...)."
    return True, "OK"

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False

def check_login_lockout(identifier: str) -> tuple[bool, str]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT attempts, last_failed FROM login_attempts WHERE identifier = ?", (identifier.lower(),))
    row = cursor.fetchone()
    conn.close()

    if row:
        attempts, last_failed = row["attempts"], row["last_failed"]
        if attempts >= MAX_LOGIN_ATTEMPTS:
            remaining = LOCKOUT_TIME_SECONDS - (time.time() - last_failed)
            if remaining > 0:
                mins = int(remaining // 60) + 1
                return True, f"🔒 Account locked due to multiple failed login attempts. Please wait {mins} minute(s)."
            else:
                # Reset expired lockout
                clear_failed_logins(identifier)
    return False, ""

def record_failed_login(identifier: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT attempts FROM login_attempts WHERE identifier = ?", (identifier.lower(),))
    row = cursor.fetchone()
    now = time.time()
    if row:
        cursor.execute("UPDATE login_attempts SET attempts = attempts + 1, last_failed = ? WHERE identifier = ?", (now, identifier.lower()))
    else:
        cursor.execute("INSERT INTO login_attempts (identifier, attempts, last_failed) VALUES (?, 1, ?)", (identifier.lower(), now))
    conn.commit()
    conn.close()
    time.sleep(0.5)  # Artificial delay to neutralize brute force & timing attacks

def clear_failed_logins(identifier: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM login_attempts WHERE identifier = ?", (identifier.lower(),))
    conn.commit()
    conn.close()

# --- 2FA & Backup Codes Helpers ---

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

def verify_and_consume_backup_code(user_id: int, input_code: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT backup_codes_json FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if not row or not row["backup_codes_json"]:
        conn.close()
        return False
    hashed_list = json.loads(row["backup_codes_json"])
    target_hash = hash_backup_code(input_code)
    if target_hash in hashed_list:
        hashed_list.remove(target_hash)
        cursor.execute("UPDATE users SET backup_codes_json = ? WHERE id = ?", (json.dumps(hashed_list), user_id))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

def generate_totp_qr(username: str, secret: str) -> bytes:
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=username, issuer_name="Chess Notation Converter")
    qr = qrcode.make(uri)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    return buf.getvalue()

# --- User & Notation Database Operations ---

SECURITY_QUESTIONS = [
    "What was the name of your first pet?",
    "What city were you born in?",
    "What is your mother's maiden name?",
    "What was the name of your first school?",
    "What is your favorite book?",
    "What is the name of the street you grew up on?",
]

def register_user(username: str, password: str, security_question: str = "", security_answer: str = "") -> tuple[bool, str]:
    val_u, msg_u = validate_username(username)
    if not val_u:
        return False, msg_u
    val_p, msg_p = validate_password_strength(password)
    if not val_p:
        return False, msg_p
    if not security_question.strip() or not security_answer.strip():
        return False, "Please select a security question and provide an answer."

    conn = get_db()
    cursor = conn.cursor()
    try:
        p_hash = hash_password(password)
        answer_hash = hash_password(security_answer.strip().lower())
        cursor.execute(
            "INSERT INTO users (username, password_hash, security_question, security_answer_hash) VALUES (?, ?, ?, ?)",
            (username.strip(), p_hash, security_question.strip(), answer_hash),
        )
        conn.commit()
        conn.close()
        return True, "User registered successfully! You can now log in."
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Username is already taken. Please choose another."
    except Exception as exc:
        conn.close()
        return False, f"Registration error: {exc}"

def reset_password_with_security_question(username: str, answer: str, new_password: str) -> tuple[bool, str]:
    val_p, msg_p = validate_password_strength(new_password)
    if not val_p:
        return False, msg_p
    user = get_user_by_username(username)
    if not user:
        time.sleep(0.5)  # Prevent username enumeration
        return False, "Could not verify your identity. Please check your username and answer."
    if not user.get("security_answer_hash"):
        return False, "No security question is set for this account."
    if not verify_password(answer.strip().lower(), user["security_answer_hash"]):
        record_failed_login(username)
        return False, "Could not verify your identity. Please check your username and answer."
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user["id"]))
    conn.commit()
    conn.close()
    clear_failed_logins(username)
    return True, "Password reset successfully! You can now log in with your new password."

def get_user_by_username(username: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def enable_totp_db(user_id: int, secret: str, hashed_backup_codes: list):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users
        SET totp_secret = ?, totp_enabled = 1, backup_codes_json = ?
        WHERE id = ?
    """, (secret, json.dumps(hashed_backup_codes), user_id))
    conn.commit()
    conn.close()

def disable_totp_db(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users
        SET totp_secret = NULL, totp_enabled = 0, backup_codes_json = NULL
        WHERE id = ?
    """, (user_id,))
    conn.commit()
    conn.close()

def save_notation_db(user_id: int, title: str, game_data: dict, pgn_text: str, csv_text: str) -> tuple[bool, str]:
    if not title.strip():
        return False, "Please enter a title for your notation."
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO saved_notations (user_id, title, event, date, white_player, black_player, result, pgn_text, csv_text, game_data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            title.strip(),
            game_data.get("event"),
            game_data.get("date"),
            game_data.get("white_player"),
            game_data.get("black_player"),
            game_data.get("result"),
            pgn_text,
            csv_text,
            json.dumps(game_data),
        ))
        conn.commit()
        conn.close()
        return True, "Notation saved successfully to your account!"
    except Exception as exc:
        conn.close()
        return False, f"Failed to save notation: {exc}"

def get_user_notations_db(user_id: int) -> list:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM saved_notations WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_notation_db(user_id: int, notation_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    # Scoped strictly by user_id to prevent IDOR / unauthorized deletion
    cursor.execute("DELETE FROM saved_notations WHERE id = ? AND user_id = ?", (notation_id, user_id))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# ─────────────────────────────────────────────
# Vision & Chess Helpers (Original Core)
# ─────────────────────────────────────────────

MEDIA_TYPE_MAP = {
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
    "webp": "image/webp",
}

EXTRACT_PROMPT = """You are a chess notation expert. Carefully analyse this handwritten chess score sheet image and extract EVERY single move written on it.

IMPORTANT: Read ALL rows on the score sheet from the very first row to the very last row. Do NOT stop early. Chess score sheets often have 30+ rows — you must read every row that has a move written in it, even if the handwriting is difficult.

Return ONLY a valid JSON object — no markdown, no extra text — in exactly this shape:
{
  "white_player": "<name or null>",
  "black_player": "<name or null>",
  "event":        "<tournament/event name or null>",
  "date":         "<YYYY.MM.DD or null>",
  "result":       "<1-0 | 0-1 | 1/2-1/2 | * | null>",
  "moves":        ["e4", "e5", "Nf3", "Nc6", ...]
}

Rules:
- `moves` must alternate White / Black in Standard Algebraic Notation (SAN).
- If a move is illegible, use the string "?" as a placeholder — but keep reading subsequent rows.
- Do NOT add move numbers, dots, or annotations into the moves array.
- Do NOT include the game result (1-0, 0-1, 1/2-1/2, *) in the moves array — put it in the "result" field only.
- Extract only what is visibly written; never invent moves.
- Read EVERY row from top to bottom. Do not skip any row."""

DEFAULT_MODEL = "gemini-2.5-flash"
_BLOCKLIST = ("live", "embedding", "tts", "image", "nano-banana", "preview", "experimental")

def list_vision_models(api_key: str) -> list:
    try:
        client = genai.Client(api_key=api_key)
        names = []
        for m in client.models.list():
            name = m.name.replace("models/", "") if m.name.startswith("models/") else m.name
            if "flash" not in name:
                continue
            if any(bad in name.lower() for bad in _BLOCKLIST):
                continue
            names.append(name)
        names.sort(reverse=True)
        return names if names else [DEFAULT_MODEL]
    except Exception:
        return [DEFAULT_MODEL]

def extract_moves(image_bytes: bytes, media_type: str, api_key: str, model: str) -> dict:
    client = genai.Client(api_key=api_key)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=media_type)

    last_exc = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[image_part, EXTRACT_PROMPT],
            )
            break
        except Exception as exc:
            last_exc = exc
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                wait = 20
                m = re.search(r"retry[^\d]*(\d+)", msg, re.I)
                if m:
                    wait = int(m.group(1)) + 2
                time.sleep(wait)
            else:
                raise
    else:
        raise last_exc

    raw = response.text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
    return json.loads(raw)

def build_pgn(game_data: dict) -> tuple:
    game = chess.pgn.Game()
    game.headers["Event"]  = game_data.get("event")        or "?"
    game.headers["Date"]   = game_data.get("date")         or date.today().strftime("%Y.%m.%d")
    game.headers["White"]  = game_data.get("white_player") or "?"
    game.headers["Black"]  = game_data.get("black_player") or "?"
    result = game_data.get("result") or "*"
    game.headers["Result"] = result

    board  = game.board()
    node   = game
    errors = []
    moves  = game_data.get("moves", [])
    validated = 0

    for idx, san in enumerate(moves, start=1):
        if san == "?":
            errors.append(f"Move {idx}: illegible")
            break
        try:
            move = board.parse_san(san)
            node = node.add_variation(move)
            board.push(move)
            validated += 1
        except Exception as exc:
            errors.append(f"Move {idx} ({san}): {exc}")
            break

    buf = io.StringIO()
    print(game, file=buf, end="\n")
    pgn_raw = buf.getvalue()

    remaining = moves[validated:]
    if remaining:
        pgn_raw = pgn_raw.rstrip()
        if pgn_raw.endswith(result):
            pgn_raw = pgn_raw[: -len(result)].rstrip()

        for i, san in enumerate(remaining):
            half = validated + i
            if half % 2 == 0:
                move_num = half // 2 + 1
                pgn_raw += f" {move_num}. {san}"
            else:
                pgn_raw += f" {san}"

        pgn_raw += f" {result}\n"

    pgn_raw = _reformat_pgn_moves(pgn_raw)
    return pgn_raw, errors

def _reformat_pgn_moves(pgn_text: str) -> str:
    lines = pgn_text.split("\n")
    header_lines = []
    move_text = ""
    in_moves = False
    for line in lines:
        if not in_moves:
            header_lines.append(line)
            if line == "" and header_lines and any(l.startswith("[") for l in header_lines):
                in_moves = True
        else:
            move_text += " " + line
    move_text = move_text.strip()
    if not move_text:
        return pgn_text
    parts = re.split(r'(\d+\.)', move_text)
    formatted_moves = []
    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if re.match(r'\d+\.$', part):
            rest = parts[i + 1].strip() if i + 1 < len(parts) else ""
            formatted_moves.append(f"{part} {rest}")
            i += 2
        else:
            if part:
                formatted_moves.append(part)
            i += 1
    return "\n".join(header_lines) + "\n".join(formatted_moves) + "\n"

def build_csv(game_data: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Move Number", "White", "Black"])
    moves = game_data.get("moves", [])
    for i in range(0, len(moves), 2):
        white = moves[i]     if i     < len(moves) else ""
        black = moves[i + 1] if i + 1 < len(moves) else ""
        writer.writerow([i // 2 + 1, white, black])
    return buf.getvalue()

def format_moves_display(moves: list) -> str:
    pairs = []
    for i in range(0, len(moves), 2):
        w = moves[i]     if i     < len(moves) else ""
        b = moves[i + 1] if i + 1 < len(moves) else ""
        pairs.append(f"{i // 2 + 1}. {w} {b}")
    return "  ".join(pairs)


# ─────────────────────────────────────────────
# Streamlit UI & Session Management
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="Chess Notation Converter",
    page_icon="♟️",
    layout="centered",
)

# Hide the "Press Enter to apply" helper text on text inputs
st.markdown("""
    <style>
        div[data-testid="InputInstructions"] { display: none; }
        /* Fix: selectbox dropdown clipped in sidebar tabs */
        [data-testid="stSidebar"] [role="tabpanel"] {
            overflow: visible !important;
        }
        [data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
            overflow: visible !important;
        }
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            overflow: visible !important;
        }
        div[data-baseweb="popover"] {
            overflow: visible !important;
        }
        ul[role="listbox"] {
            max-height: 400px !important;
        }
    </style>
""", unsafe_allow_html=True)

# Initialize Session State Variables
if "user" not in st.session_state:
    st.session_state["user"] = None  # Holds dict: {"id": int, "username": str, "totp_enabled": int}
if "pending_login_user" not in st.session_state:
    st.session_state["pending_login_user"] = None
if "setup_2fa_secret" not in st.session_state:
    st.session_state["setup_2fa_secret"] = None
if "setup_2fa_raw_codes" not in st.session_state:
    st.session_state["setup_2fa_raw_codes"] = None
if "setup_2fa_hashed_codes" not in st.session_state:
    st.session_state["setup_2fa_hashed_codes"] = None


# ── Sidebar Authentication & Settings ──────────────────────────────────────
with st.sidebar:
    st.title("♟️ Chess Portal")

    # User Account Card
    current_user = st.session_state["user"]
    if current_user:
        st.success(f"👤 **Logged in as:** `{current_user['username']}`")
        if current_user.get("totp_enabled"):
            st.caption("🔒 2-Factor Authentication: **ENABLED**")
        else:
            st.caption("⚠️ 2-Factor Authentication: **DISABLED**")

        if st.button("🚪 Sign Out", use_container_width=True):
            st.session_state.clear()
            st.rerun()

        st.divider()

        # ── 2FA Security Management Tab ──────────────────────────────────
        with st.expander("🛡️ Security & 2FA Settings", expanded=False):
            if not current_user.get("totp_enabled"):
                st.subheader("Enable 2-Factor Authentication")
                st.markdown("Enhance your account security with an authenticator app (Google Authenticator, Authy, 1Password, etc.).")

                if not st.session_state["setup_2fa_secret"]:
                    if st.button("🚀 Begin 2FA Setup"):
                        secret = pyotp.random_base32()
                        raw_codes, hashed_codes = generate_backup_codes(8)
                        st.session_state["setup_2fa_secret"] = secret
                        st.session_state["setup_2fa_raw_codes"] = raw_codes
                        st.session_state["setup_2fa_hashed_codes"] = hashed_codes
                        st.rerun()

                if st.session_state["setup_2fa_secret"]:
                    secret = st.session_state["setup_2fa_secret"]
                    raw_codes = st.session_state["setup_2fa_raw_codes"]
                    hashed_codes = st.session_state["setup_2fa_hashed_codes"]

                    st.markdown("**Step 1: Scan QR Code**")
                    qr_bytes = generate_totp_qr(current_user["username"], secret)
                    st.image(qr_bytes, caption="Scan with your Authenticator App", width=220)
                    st.code(f"Secret Key: {secret}", language="text")

                    st.markdown("**Step 2: Save Backup Codes**")
                    st.warning("⚠️ Save these backup codes in a safe place! If you lose your phone, you can use these codes to log in.")
                    codes_str = "\n".join(raw_codes)
                    st.code(codes_str, language="text")
                    st.download_button(
                        "📥 Download Backup Codes",
                        f"CHESS APP 2FA BACKUP CODES FOR {current_user['username']}:\n\n" + codes_str + "\n\nEach code can only be used once.",
                        file_name=f"chess_2fa_backup_codes_{current_user['username']}.txt",
                        mime="text/plain",
                    )

                    st.markdown("**Step 3: Verify 6-digit Code**")
                    verify_input = st.text_input("Enter 6-digit Authenticator Code to Activate", max_chars=6, key="enable_2fa_code")
                    if st.button("✅ Confirm & Activate 2FA"):
                        totp = pyotp.TOTP(secret)
                        if totp.verify(verify_input.strip()):
                            enable_totp_db(current_user["id"], secret, hashed_codes)
                            current_user["totp_enabled"] = 1
                            st.session_state["user"] = current_user
                            st.session_state["setup_2fa_secret"] = None
                            st.session_state["setup_2fa_raw_codes"] = None
                            st.session_state["setup_2fa_hashed_codes"] = None
                            st.success("🎉 2FA successfully activated!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("❌ Invalid 6-digit code. Please try again.")

            else:
                st.subheader("Disable 2-Factor Authentication")
                st.warning("Disabling 2FA reduces account security.")
                disable_pwd = st.text_input("Confirm Account Password to Disable 2FA", type="password", key="disable_2fa_pwd")
                if st.button("🔴 Disable 2FA"):
                    db_u = get_user_by_username(current_user["username"])
                    if db_u and verify_password(disable_pwd, db_u["password_hash"]):
                        disable_totp_db(current_user["id"])
                        current_user["totp_enabled"] = 0
                        st.session_state["user"] = current_user
                        st.success("2FA has been disabled.")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("❌ Incorrect password.")

    else:
        # ── Guest Authentication Controls (Login / Signup / Forgot Password) ──
        auth_tab1, auth_tab2, auth_tab3 = st.tabs(["🔑 Log In", "📝 Sign Up", "🔄 Forgot?"])

        with auth_tab1:
            if not st.session_state["pending_login_user"]:
                st.subheader("Account Login")
                login_user = st.text_input("Username", key="login_username_input").strip()
                login_pass = st.text_input("Password", type="password", key="login_password_input")

                if st.button("Sign In", type="primary", use_container_width=True):
                    if not login_user or not login_pass:
                        st.error("Please fill in both fields.")
                    else:
                        # Check rate limiting lockout first
                        is_locked, lockout_msg = check_login_lockout(login_user)
                        if is_locked:
                            st.error(lockout_msg)
                        else:
                            user_data = get_user_by_username(login_user)
                            if user_data and verify_password(login_pass, user_data["password_hash"]):
                                clear_failed_logins(login_user)
                                # Check if 2FA is required
                                if user_data["totp_enabled"]:
                                    st.session_state["pending_login_user"] = user_data
                                    st.rerun()
                                else:
                                    # Complete login
                                    st.session_state["user"] = {
                                        "id": user_data["id"],
                                        "username": user_data["username"],
                                        "totp_enabled": user_data["totp_enabled"],
                                    }
                                    st.success(f"Welcome back, {user_data['username']}!")
                                    time.sleep(0.5)
                                    st.rerun()
                            else:
                                record_failed_login(login_user)
                                st.error("❌ Invalid username or password.")

            else:
                # ── 2FA Step 2 Login Verification ────────────────────────
                pending_u = st.session_state["pending_login_user"]
                st.subheader("🔐 2-Factor Verification")
                st.info(f"Account `{pending_u['username']}` is protected with 2FA.")

                totp_code_input = st.text_input("Enter 6-Digit Authenticator Code", max_chars=6, key="login_totp_code")
                backup_code_input = st.text_input("OR Enter Backup Code (XXXX-XXXX)", key="login_backup_code")

                c_v1, c_v2 = st.columns(2)
                if c_v1.button("Verify Code", type="primary", use_container_width=True):
                    totp_verified = False
                    if totp_code_input.strip():
                        totp = pyotp.TOTP(pending_u["totp_secret"])
                        if totp.verify(totp_code_input.strip()):
                            totp_verified = True

                    backup_verified = False
                    if not totp_verified and backup_code_input.strip():
                        if verify_and_consume_backup_code(pending_u["id"], backup_code_input.strip()):
                            backup_verified = True

                    if totp_verified or backup_verified:
                        st.session_state["user"] = {
                            "id": pending_u["id"],
                            "username": pending_u["username"],
                            "totp_enabled": pending_u["totp_enabled"],
                        }
                        st.session_state["pending_login_user"] = None
                        if backup_verified:
                            st.warning("⚠️ You used a single-use backup code to log in. That code is now invalidated.")
                        st.success(f"Verification successful! Logged in as {pending_u['username']}.")
                        time.sleep(1)
                        st.rerun()
                    else:
                        record_failed_login(pending_u["username"])
                        st.error("❌ Invalid 2FA or Backup code.")

                if c_v2.button("Cancel", use_container_width=True):
                    st.session_state["pending_login_user"] = None
                    st.rerun()

        with auth_tab2:
            st.subheader("Create New Account")
            new_u = st.text_input("Choose Username", key="signup_u").strip()
            new_p = st.text_input("Choose Password", type="password", key="signup_p")
            new_p2 = st.text_input("Confirm Password", type="password", key="signup_p2")
            sec_q = st.selectbox("Security Question", SECURITY_QUESTIONS, key="signup_sec_q")
            sec_a = st.text_input("Your Answer", key="signup_sec_a", placeholder="Answer (case-insensitive)")

            if st.button("Create Account", type="primary", use_container_width=True):
                if new_p != new_p2:
                    st.error("❌ Passwords do not match.")
                else:
                    ok, msg = register_user(new_u, new_p, sec_q, sec_a)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(f"❌ {msg}")

        with auth_tab3:
            st.subheader("Reset Password")
            reset_user = st.text_input("Your Username", key="reset_username").strip()
            # Show the security question for the entered username
            reset_question_label = ""
            if reset_user:
                reset_u_data = get_user_by_username(reset_user)
                if reset_u_data and reset_u_data.get("security_question"):
                    reset_question_label = reset_u_data["security_question"]
            if reset_question_label:
                st.info(f"**Security Question:** {reset_question_label}")
            reset_answer = st.text_input("Security Answer", key="reset_answer", placeholder="Answer (case-insensitive)")
            reset_new_p = st.text_input("New Password", type="password", key="reset_new_p")
            reset_new_p2 = st.text_input("Confirm New Password", type="password", key="reset_new_p2")

            if st.button("Reset Password", type="primary", use_container_width=True):
                if not reset_user or not reset_answer:
                    st.error("Please fill in your username and security answer.")
                elif reset_new_p != reset_new_p2:
                    st.error("❌ Passwords do not match.")
                else:
                    is_locked, lockout_msg = check_login_lockout(reset_user)
                    if is_locked:
                        st.error(lockout_msg)
                    else:
                        ok, msg = reset_password_with_security_question(reset_user, reset_answer, reset_new_p)
                        if ok:
                            st.success(msg)
                        else:
                            st.error(f"❌ {msg}")

    st.divider()

    # ── Sidebar Settings (API Key & Vision Model) ──────────────────────────
    st.header("⚙️ OCR Settings")
    api_key = st.text_input(
        "Google Gemini API Key",
        type="password",
        placeholder="AIza...",
        help="Get a free key at https://aistudio.google.com/app/apikey",
    )
    st.markdown(
        "🔑 **Get your free key:**\n\n"
        "1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)\n"
        "2. Sign in with your Google account\n"
        "3. Click **Create API Key**\n"
        "4. Paste it above"
    )
    st.divider()

    selected_model = DEFAULT_MODEL
    if api_key:
        if st.button("🔄 Load available models"):
            with st.spinner("Fetching models…"):
                st.session_state["available_models"] = list_vision_models(api_key)

        models = st.session_state.get("available_models", [DEFAULT_MODEL])
        default_idx = models.index(DEFAULT_MODEL) if DEFAULT_MODEL in models else 0
        selected_model = st.selectbox("Model", models, index=default_idx)
        st.caption(f"Using `{selected_model}` · Free tier · No credit card needed")




# ── Main Content Area ──────────────────────────────────────────────────────
st.title("♟️ Chess Notation Converter")
st.markdown(
    "Upload **one or more photos** of a handwritten chess score sheet and get instant "
    "**PGN** and **CSV** exports — powered by Google Gemini (free).  \n"
    "*Multi-page score sheets? Upload all pages in order.*"
)
st.divider()

if not st.session_state["user"]:
    # ── Guest gate: require login before using the app ─────────────────
    st.warning("🔒 **Please log in or create an account to use the converter.**")
    st.info("Use the sidebar on the left to **Log In** or **Sign Up** — it's free and takes seconds!")
else:
    # ── File uploader (multi-image) ────────────────────────────────────────────
    uploaded_files = st.file_uploader(
        "📷 Drop your score sheet(s) here",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        help="Upload one or more images. For multi-page sheets, upload all pages in order.",
    )

    if uploaded_files:
        cols = st.columns(min(len(uploaded_files), 4))
        for i, f in enumerate(uploaded_files):
            cols[i % len(cols)].image(f, caption=f"Page {i + 1}: {f.name}", use_container_width=True)
        st.divider()

        n = len(uploaded_files)
        label = f"🔍 Extract & Convert ({n} image{'s' if n > 1 else ''})"
        if st.button(label, type="primary", use_container_width=True):

            if not api_key:
                st.error("❌ Please enter your Gemini API key in the sidebar first.")
                st.stop()

            all_moves = []
            metadata = {}
            progress = st.progress(0, text="Starting extraction…")

            for idx, file in enumerate(uploaded_files):
                page_label = f"Page {idx + 1}/{n}: {file.name}"
                progress.progress((idx) / n, text=f"Reading {page_label}…")

                try:
                    image_bytes = file.read()
                    ext         = file.name.rsplit(".", 1)[-1].lower()
                    media_type  = MEDIA_TYPE_MAP.get(ext, "image/jpeg")
                    page_data   = extract_moves(image_bytes, media_type, api_key, selected_model)
                except json.JSONDecodeError:
                    st.error(f"❌ Could not parse notation from {page_label}. Try a clearer photo.")
                    st.stop()
                except Exception as exc:
                    st.error(f"❌ Extraction failed for {page_label}: {exc}")
                    st.stop()

                if idx == 0:
                    metadata = {
                        "white_player": page_data.get("white_player"),
                        "black_player": page_data.get("black_player"),
                        "event":        page_data.get("event"),
                        "date":         page_data.get("date"),
                        "result":       page_data.get("result"),
                    }

                page_moves = page_data.get("moves", [])
                all_moves.extend(page_moves)
                st.toast(f"✅ {page_label} — {len(page_moves)} half-moves")

            progress.progress(1.0, text="Done!")

            game_data = {**metadata, "moves": all_moves}
            pgn_str, pgn_errors = build_pgn(game_data)
            csv_str              = build_csv(game_data)

            st.session_state["game_data"]  = game_data
            st.session_state["pgn_str"]    = pgn_str
            st.session_state["pgn_editor"] = pgn_str
            st.session_state["csv_str"]    = csv_str
            st.session_state["pgn_errors"] = pgn_errors
            st.session_state["num_images"] = n

        # ── Display extracted results ──────────────────────────────────────
        if "game_data" in st.session_state:
            game_data  = st.session_state["game_data"]
            pgn_str    = st.session_state["pgn_str"]
            csv_str    = st.session_state["csv_str"]
            pgn_errors = st.session_state["pgn_errors"]
            n_imgs     = st.session_state["num_images"]

            st.success(f"✅ Notation extracted from {n_imgs} image{'s' if n_imgs > 1 else ''}!")

            with st.expander("📋 Extracted game info", expanded=True):
                c1, c2 = st.columns(2)
                c1.metric("White",  game_data.get("white_player") or "—")
                c2.metric("Black",  game_data.get("black_player") or "—")
                c1.metric("Event",  game_data.get("event")        or "—")
                c2.metric("Result", game_data.get("result")       or "—")
                moves = game_data.get("moves", [])
                st.write(f"**{len(moves)} half-moves ({len(moves)//2 + len(moves)%2} full moves) detected**")
                if n_imgs > 1:
                    st.caption(f"Merged from {n_imgs} images")
                st.code(format_moves_display(moves), language="text")

            if pgn_errors:
                st.warning("⚠️ Some moves couldn't be validated:\n- " + "\n- ".join(pgn_errors))

            st.subheader("✏️ Edit PGN")
            st.caption("Review and edit the generated PGN before downloading or saving.")
            line_count = pgn_str.count("\n") + 1
            area_height = max(line_count * 28, 150)
            edited_pgn = st.text_area(
                "PGN content",
                height=area_height,
                key="pgn_editor",
                label_visibility="collapsed",
            )

            st.subheader("📥 Download & Save Options")
            dl1, dl2 = st.columns(2)
            dl1.download_button("⬇️ Download PGN", edited_pgn, "game.pgn", "text/plain",  use_container_width=True)
            dl2.download_button("⬇️ Download CSV", csv_str, "game.csv", "text/csv",    use_container_width=True)

            # ── Save Notation to User Account ──────────────────────────────────
            st.divider()
            st.markdown("### 💾 Save to Account")
            sav_col1, sav_col2 = st.columns([3, 1])
            default_title = f"{game_data.get('white_player') or 'White'} vs {game_data.get('black_player') or 'Black'} ({game_data.get('date') or date.today().strftime('%Y.%m.%d')})"
            save_title_input = sav_col1.text_input("Notation Title", value=default_title, key="save_title_input")
            if sav_col2.button("💾 Save", type="primary", use_container_width=True):
                ok, msg = save_notation_db(
                    st.session_state["user"]["id"],
                    save_title_input,
                    game_data,
                    edited_pgn,
                    csv_str
                )
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    else:
        st.info("👆 Upload a score sheet image above to get started.")

# ── Saved Notations Section (For Authenticated Users) ──────────────────────
if st.session_state["user"]:
    st.divider()
    st.header("📂 My Saved Notations")
    saved_games = get_user_notations_db(st.session_state["user"]["id"])

    if not saved_games:
        st.caption("You have no saved notations yet. Extract a score sheet above and click **Save**!")
    else:
        st.write(f"You have **{len(saved_games)}** saved notation(s):")
        for game in saved_games:
            with st.expander(f"♟️ {game['title']} — {game['created_at'][:10]}"):
                mc1, mc2 = st.columns(2)
                mc1.write(f"**White:** {game['white_player'] or '?'}")
                mc2.write(f"**Black:** {game['black_player'] or '?'}")
                mc1.write(f"**Event:** {game['event'] or '?'}")
                mc2.write(f"**Result:** {game['result'] or '?'}")

                st.code(game['pgn_text'], language="text")

                act1, act2, act3 = st.columns(3)
                act1.download_button(
                    "⬇️ PGN",
                    game['pgn_text'],
                    file_name=f"{game['title'].replace(' ', '_')}.pgn",
                    mime="text/plain",
                    key=f"dl_pgn_{game['id']}",
                    use_container_width=True
                )
                act2.download_button(
                    "⬇️ CSV",
                    game['csv_text'],
                    file_name=f"{game['title'].replace(' ', '_')}.csv",
                    mime="text/csv",
                    key=f"dl_csv_{game['id']}",
                    use_container_width=True
                )
                if act3.button("🗑️ Delete", key=f"del_{game['id']}", type="secondary", use_container_width=True):
                    if delete_notation_db(st.session_state["user"]["id"], game["id"]):
                        st.success(f"Deleted '{game['title']}'")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("Failed to delete notation.")