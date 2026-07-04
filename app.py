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
from datetime import date

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

MEDIA_TYPE_MAP = {
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
    "webp": "image/webp",
}

EXTRACT_PROMPT = """You are a chess notation expert. Carefully analyse this handwritten chess score sheet image and extract every move.

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
- If a move is illegible, use the string "?" as a placeholder.
- Do NOT add move numbers, dots, or annotations into the moves array.
- Extract only what is visibly written; never invent moves."""

DEFAULT_MODEL = "gemini-2.5-flash"


# Models to exclude — too slow, experimental, or not vision-capable
_BLOCKLIST = ("live", "embedding", "tts", "image", "nano-banana", "preview", "experimental")

def list_vision_models(api_key: str) -> list:
    """Return only fast Flash model names suitable for this task."""
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
    game.headers["Result"] = game_data.get("result")       or "*"

    board  = game.board()
    node   = game
    errors = []

    for idx, san in enumerate(game_data.get("moves", []), start=1):
        if san == "?":
            errors.append(f"Move {idx}: illegible — stopped here")
            break
        try:
            move = board.parse_san(san)
            node = node.add_variation(move)
            board.push(move)
        except Exception as exc:
            errors.append(f"Move {idx} ({san}): {exc}")
            break

    buf = io.StringIO()
    print(game, file=buf, end="\n")
    return buf.getvalue(), errors


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
# Streamlit UI
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="Chess Notation Converter",
    page_icon="♟️",
    layout="centered",
)

st.title("♟️ Chess Notation Converter")
st.markdown(
    "Upload a **photo of a handwritten chess score sheet** and get instant "
    "**PGN** and **CSV** exports — powered by Google Gemini (free)."
)
st.divider()

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
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

    # Dynamic model selector — loads once API key is entered
    selected_model = DEFAULT_MODEL
    if api_key:
        if st.button("🔄 Load available models"):
            with st.spinner("Fetching models…"):
                st.session_state["available_models"] = list_vision_models(api_key)

        models = st.session_state.get("available_models", [DEFAULT_MODEL])
        default_idx = models.index(DEFAULT_MODEL) if DEFAULT_MODEL in models else 0
        selected_model = st.selectbox("Model", models, index=default_idx)
        st.caption(f"Using `{selected_model}` · Free tier · No credit card needed")

# ── File uploader ──────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "📷 Drop your score sheet here",
    type=["jpg", "jpeg", "png", "webp"],
    help="Works with photos, scans, or any clear image of a score sheet.",
)

if uploaded:
    st.image(uploaded, caption="Uploaded score sheet", use_column_width=True)
    st.divider()

    if st.button("🔍 Extract & Convert", type="primary", use_container_width=True):

        if not api_key:
            st.error("❌ Please enter your Gemini API key in the sidebar first.")
            st.stop()

        with st.spinner(f"Reading handwriting with `{selected_model}`…"):
            try:
                image_bytes = uploaded.read()
                ext         = uploaded.name.rsplit(".", 1)[-1].lower()
                media_type  = MEDIA_TYPE_MAP.get(ext, "image/jpeg")
                game_data   = extract_moves(image_bytes, media_type, api_key, selected_model)
            except json.JSONDecodeError:
                st.error("❌ Could not parse the notation. Try a clearer / higher-resolution photo.")
                st.stop()
            except Exception as exc:
                st.error(f"❌ Extraction failed: {exc}")
                st.stop()

        st.success("✅ Notation extracted!")

        # Game metadata
        with st.expander("📋 Extracted game info", expanded=True):
            c1, c2 = st.columns(2)
            c1.metric("White",  game_data.get("white_player") or "—")
            c2.metric("Black",  game_data.get("black_player") or "—")
            c1.metric("Event",  game_data.get("event")        or "—")
            c2.metric("Result", game_data.get("result")       or "—")
            moves = game_data.get("moves", [])
            st.write(f"**{len(moves)} half-moves ({len(moves)//2 + len(moves)%2} full moves) detected**")
            st.code(format_moves_display(moves), language="text")

        pgn_str, pgn_errors = build_pgn(game_data)
        csv_str             = build_csv(game_data)

        if pgn_errors:
            st.warning("⚠️ Some moves couldn't be validated:\n- " + "\n- ".join(pgn_errors))

        # Downloads
        st.subheader("📥 Download")
        dl1, dl2 = st.columns(2)
        dl1.download_button("⬇️ Download PGN", pgn_str, "game.pgn", "text/plain",  use_container_width=True)
        dl2.download_button("⬇️ Download CSV", csv_str, "game.csv", "text/csv",    use_container_width=True)

        # Previews
        st.subheader("👁️ Preview")
        tab_pgn, tab_csv = st.tabs(["PGN", "CSV"])
        with tab_pgn:
            st.code(pgn_str, language="text")
        with tab_csv:
            st.code(csv_str, language="text")

else:
    st.info("👆 Upload a score sheet image above to get started.")


# ── Credits ────────────────────────────────────────────────────────────────
st.divider()
st.markdown(
    """
    <div style="text-align: center; padding: 1rem 0 0.5rem 0; opacity: 0.7;">
        <p style="margin: 0; font-size: 0.85rem;">Created by</p>
        <p style="margin: 0.25rem 0 0 0; font-size: 1rem; font-weight: 600;">
            Abhyuday Khodpe & Anirban Goswami
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)