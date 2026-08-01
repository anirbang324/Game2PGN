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
    result = game_data.get("result") or "*"
    game.headers["Result"] = result

    board  = game.board()
    node   = game
    errors = []
    moves  = game_data.get("moves", [])
    validated = 0                       # count of half-moves validated

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

    # Build PGN from the validated portion
    buf = io.StringIO()
    print(game, file=buf, end="\n")
    pgn_raw = buf.getvalue()

    # Append any remaining unvalidated moves as raw PGN text
    remaining = moves[validated:]
    if remaining:
        # Strip the trailing result from the validated PGN so we can append moves
        pgn_raw = pgn_raw.rstrip()
        if pgn_raw.endswith(result):
            pgn_raw = pgn_raw[: -len(result)].rstrip()

        for i, san in enumerate(remaining):
            half = validated + i          # 0-indexed half-move number
            if half % 2 == 0:             # White's move
                move_num = half // 2 + 1
                pgn_raw += f" {move_num}. {san}"
            else:                         # Black's move
                pgn_raw += f" {san}"

        pgn_raw += f" {result}\n"

    # Reformat: put each move pair on its own line for easier editing
    pgn_raw = _reformat_pgn_moves(pgn_raw)
    return pgn_raw, errors


def _reformat_pgn_moves(pgn_text: str) -> str:
    """Rewrite the move-text section so each numbered move starts on a new line."""
    lines = pgn_text.split("\n")
    header_lines = []
    move_text = ""
    in_moves = False
    for line in lines:
        if not in_moves:
            header_lines.append(line)
            # Moves start after the last header (blank line after [headers])
            if line == "" and header_lines and any(l.startswith("[") for l in header_lines):
                in_moves = True
        else:
            move_text += " " + line
    move_text = move_text.strip()
    if not move_text:
        return pgn_text
    # Split on move numbers:  "1." "2." etc.
    parts = re.split(r'(\d+\.)', move_text)
    formatted_moves = []
    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if re.match(r'\d+\.$', part):
            # Combine move number with the following moves
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
# Streamlit UI
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="Chess Notation Converter",
    page_icon="♟️",
    layout="centered",
)

st.title("♟️ Chess Notation Converter")
st.markdown(
    "Upload **one or more photos** of a handwritten chess score sheet and get instant "
    "**PGN** and **CSV** exports — powered by Google Gemini (free).  \n"
    "*Multi-page score sheets? Upload all pages in order.*"
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

# ── File uploader (multi-image) ────────────────────────────────────────────
uploaded_files = st.file_uploader(
    "📷 Drop your score sheet(s) here",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
    help="Upload one or more images. For multi-page sheets, upload all pages in order.",
)

if uploaded_files:
    # Show thumbnails in a grid
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

        # ── Process each image sequentially ───────────────────────────
        all_moves = []
        metadata = {}          # take metadata from first image
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

            # Keep metadata from the first image only
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

        # Combine into one game_data dict and persist in session state
        game_data = {**metadata, "moves": all_moves}
        pgn_str, pgn_errors = build_pgn(game_data)
        csv_str              = build_csv(game_data)

        st.session_state["game_data"]  = game_data
        st.session_state["pgn_str"]    = pgn_str
        st.session_state["pgn_editor"] = pgn_str   # seed the editor widget
        st.session_state["csv_str"]    = csv_str
        st.session_state["pgn_errors"] = pgn_errors
        st.session_state["num_images"] = n

    # ── Show results if available in session state ─────────────────────
    if "game_data" in st.session_state:
        game_data  = st.session_state["game_data"]
        pgn_str    = st.session_state["pgn_str"]
        csv_str    = st.session_state["csv_str"]
        pgn_errors = st.session_state["pgn_errors"]
        n_imgs     = st.session_state["num_images"]

        st.success(f"✅ Notation extracted from {n_imgs} image{'s' if n_imgs > 1 else ''}!")

        # Game metadata
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

        # Editable PGN
        st.subheader("✏️ Edit PGN")
        st.caption("Review and edit the generated PGN before downloading.")
        # Auto-size: ~28px per line, min 150 — no max so all rows are visible
        line_count = pgn_str.count("\n") + 1
        area_height = max(line_count * 28, 150)
        edited_pgn = st.text_area(
            "PGN content",
            height=area_height,
            key="pgn_editor",
            label_visibility="collapsed",
        )

        # Downloads — uses edited PGN
        st.subheader("📥 Download")
        dl1, dl2 = st.columns(2)
        dl1.download_button("⬇️ Download PGN", edited_pgn, "game.pgn", "text/plain",  use_container_width=True)
        dl2.download_button("⬇️ Download CSV", csv_str, "game.csv", "text/csv",    use_container_width=True)

else:
    st.info("👆 Upload a score sheet image above to get started.")