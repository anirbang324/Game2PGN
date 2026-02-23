"""
Chess Notation to PGN Converter

This module converts photos of handwritten or printed chess notation into 
editable PGN (Portable Game Notation) format with user verification at each step.

The conversion process includes:
1. Image preprocessing and enhancement
2. OCR (Optical Character Recognition) for text extraction
3. Chess notation parsing and validation
4. User verification interface
5. PGN file generation

Author: Chess Notation Converter Project
Date: 2026-02-04
"""

import os
import sys
import re
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

try:
    from PIL import Image, ImageEnhance
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("Warning: PIL/Pillow not available")

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("Warning: OpenCV not available")

try:
    import pytesseract
    import os as _os
    # Auto-detect Tesseract executable on Windows if not already on PATH
    _tess_candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for _p in _tess_candidates:
        if _os.path.exists(_p):
            pytesseract.pytesseract.tesseract_cmd = _p
            break
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    print("Warning: pytesseract not available")

try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False
    print("Warning: easyocr not available")

try:
    import chess
    CHESS_AVAILABLE = True
except ImportError:
    CHESS_AVAILABLE = False
    print("Warning: python-chess not available")


class NotationType(Enum):
    """Types of chess notation formats"""
    ALGEBRAIC = "algebraic"  # Standard algebraic notation (e.g., e4, Nf3)
    LONG_ALGEBRAIC = "long_algebraic"  # Long algebraic (e.g., e2-e4)
    DESCRIPTIVE = "descriptive"  # Older descriptive notation (e.g., P-K4)


@dataclass
class ChessMove:
    """Represents a single chess move with metadata"""
    move_number: int
    white_move: str
    black_move: Optional[str] = None
    white_comment: Optional[str] = None
    black_comment: Optional[str] = None
    confidence: float = 1.0  # OCR confidence score (0.0 to 1.0)


@dataclass
class GameMetadata:
    """PGN header information for a chess game"""
    event: str = "?"
    site: str = "?"
    date: str = "????.??.??"
    round: str = "?"
    white: str = "?"
    black: str = "?"
    result: str = "*"  # 1-0, 0-1, 1/2-1/2, or *
    
    def to_pgn_headers(self) -> str:
        """Convert metadata to PGN header format"""
        headers = [
            f'[Event "{self.event}"]',
            f'[Site "{self.site}"]',
            f'[Date "{self.date}"]',
            f'[Round "{self.round}"]',
            f'[White "{self.white}"]',
            f'[Black "{self.black}"]',
            f'[Result "{self.result}"]'
        ]
        return '\n'.join(headers)


class ImagePreprocessor:
    """Handles image preprocessing and enhancement for better OCR results"""
    
    def __init__(self):
        """Initialize the image preprocessor"""
        self.supported_formats = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff']
    
    def load_image(self, image_path: str):
        """
        Load an image from the specified path
        
        Args:
            image_path: Path to the image file
            
        Returns:
            Loaded image object (requires PIL/Pillow or OpenCV)
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
        
        print(f"Loading image: {image_path}")
        
        if CV2_AVAILABLE:
            # Load with OpenCV (returns numpy array)
            image = cv2.imread(image_path)
            if image is None:
                raise ValueError(f"Failed to load image: {image_path}")
            return image
        elif PIL_AVAILABLE:
            # Load with PIL
            image = Image.open(image_path)
            return image
        else:
            raise RuntimeError("No image loading library available (install opencv-python or pillow)")
    
    def enhance_image(self, image):
        """
        Apply gentle preprocessing for handwritten chess notation.

        Uses grayscale + mild contrast + 2x upscaling for small handwriting.
        No binarization — that destroys handwriting curves for EasyOCR.

        Args:
            image: Input image (numpy ndarray from OpenCV)

        Returns:
            Enhanced image (BGR ndarray, upscaled 2x)
        """
        print("Enhancing image for OCR...")

        if not CV2_AVAILABLE:
            print("  Skipping enhancement (OpenCV not available)")
            return image

        # Upscale 2x — improves EasyOCR accuracy on small handwriting
        h, w = image.shape[:2]
        image = cv2.resize(image, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

        # Convert to grayscale
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        # Mild CLAHE to improve local contrast without destroying ink strokes
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        # Mild sharpening kernel
        kernel = np.array([[0, -0.5, 0],
                           [-0.5, 3, -0.5],
                           [0, -0.5, 0]])
        enhanced = cv2.filter2D(enhanced, -1, kernel)
        enhanced = np.clip(enhanced, 0, 255).astype(np.uint8)

        # Convert back to BGR so EasyOCR receives a 3-channel image
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

        print(f"  Applied: 2x upscale, grayscale, CLAHE, sharpen -> {enhanced.shape[1]}x{enhanced.shape[0]}")
        return enhanced
    
    def detect_text_regions(self, image) -> List[Tuple[int, int, int, int]]:
        """
        Detect regions in the image that contain text
        
        Args:
            image: Input image object
            
        Returns:
            List of bounding boxes (x, y, width, height) for text regions
        """
        # TODO: Implement text region detection
        print("Detecting text regions...")
        return []


class OCREngine:
    """
    Handles optical character recognition of chess notation.

    Uses EasyOCR as the primary engine (no external binary required).
    Falls back to Tesseract if EasyOCR is unavailable.
    """

    def __init__(self, engine: str = "auto"):
        """
        Initialize the OCR engine.

        Args:
            engine: 'easyocr' | 'tesseract' | 'auto' (tries EasyOCR first, then Tesseract)
        """
        self._easyocr_reader = None

        if engine == "auto":
            if EASYOCR_AVAILABLE:
                self.engine = "easyocr"
            elif TESSERACT_AVAILABLE:
                self.engine = "tesseract"
            else:
                self.engine = "none"
        else:
            self.engine = engine

        print(f"Initializing OCR engine: {self.engine}")

        if self.engine == "easyocr":
            print("  Loading EasyOCR model (first run may download ~100 MB)...")
            self._easyocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
            print("  EasyOCR ready.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_text(self, image, region: Optional[Tuple[int, int, int, int]] = None) -> str:
        """
        Extract text from an image or image region.

        Args:
            image: Input image (numpy ndarray or PIL Image)
            region: Optional bounding box (x, y, width, height)

        Returns:
            Extracted text string
        """
        print("Extracting text from image...")

        if region and CV2_AVAILABLE and isinstance(image, np.ndarray):
            x, y, w, h = region
            image = image[y:y + h, x:x + w]
        elif region and PIL_AVAILABLE:
            x, y, w, h = region
            image = image.crop((x, y, x + w, y + h))

        if self.engine == "easyocr":
            return self._extract_easyocr(image)
        elif self.engine == "tesseract":
            return self._extract_tesseract(image)
        else:
            print("  ERROR: No OCR engine available.")
            print("  Install EasyOCR:  pip install easyocr")
            print("  Install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki")
            return ""

    def get_confidence_scores(self, image) -> Dict[str, float]:
        """Return per-word confidence scores (EasyOCR only for now)."""
        if self.engine == "easyocr" and self._easyocr_reader is not None:
            try:
                results = self._easyocr_reader.readtext(image)
                return {text: float(conf) for (_, text, conf) in results}
            except Exception as e:
                print(f"  Error getting confidence scores: {e}")
        elif self.engine == "tesseract" and TESSERACT_AVAILABLE:
            try:
                data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
                return {
                    text: float(data['conf'][i]) / 100.0
                    for i, text in enumerate(data['text'])
                    if text.strip()
                }
            except Exception as e:
                print(f"  Error getting confidence scores: {e}")
        return {}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_all_ocr_results(self, image, n_strips: int = 4) -> list:
        """
        Run EasyOCR on N horizontal strips and merge results with Y-offset.

        Processing in strips prevents EasyOCR from missing rows in tall images.
        Uses paragraph=False to detect individual words (better for scoresheet cells).
        """
        h, w = image.shape[:2]
        strip_height = h // n_strips
        overlap = strip_height // 4

        all_results = []
        seen_positions = set()

        for i in range(n_strips):
            y_start = max(0, i * strip_height - overlap)
            y_end   = min(h, y_start + strip_height + overlap)
            strip   = image[y_start:y_end, :]

            try:
                # paragraph=False: detect individual words/tokens
                # width_ths=0.3: allow narrow bounding boxes (single letters)
                strip_results = self._easyocr_reader.readtext(
                    strip,
                    paragraph=False,
                    width_ths=0.3,
                    x_ths=0.5,
                )
            except Exception as e:
                print(f"  EasyOCR strip {i+1} error: {e}")
                continue

            for (bbox, text, conf) in strip_results:
                if not text.strip():
                    continue
                global_bbox = [[pt[0], pt[1] + y_start] for pt in bbox]

                ys = [pt[1] for pt in global_bbox]
                xs = [pt[0] for pt in global_bbox]
                x_c = (min(xs) + max(xs)) / 2
                y_c = (min(ys) + max(ys)) / 2
                key = (round(x_c / 30) * 30,
                       round(y_c / 25) * 25,
                       text.strip()[:10])
                if key in seen_positions:
                    continue
                seen_positions.add(key)
                all_results.append((global_bbox, text, conf))

        # Sort by Y then X and print all tokens for diagnostics
        all_results.sort(key=lambda r: (
            (min(pt[1] for pt in r[0]) + max(pt[1] for pt in r[0])) / 2,
            (min(pt[0] for pt in r[0]) + max(pt[0] for pt in r[0])) / 2,
        ))
        print(f"  Strip OCR: {n_strips} strips -> {len(all_results)} total tokens")
        print("  All tokens (X_centre, Y_centre, text, conf):")
        for (bbox, text, conf) in all_results:
            xs = [pt[0] for pt in bbox]; ys = [pt[1] for pt in bbox]
            print(f"    x={int((min(xs)+max(xs))/2):4d} y={int((min(ys)+max(ys))/2):4d}"
                  f"  '{text}'  ({conf:.2f})")
        return all_results

    def _extract_easyocr(self, image) -> str:
        """
        Extract text using EasyOCR with strip processing and column-aware parsing.

        Strategy:
        1. Process image in horizontal strips to capture all rows.
        2. Detect #/WHITE/BLACK column X-boundaries from header row.
        3. Classify each token to its column by X-centre.
        4. Reconstruct lines as "N. white black" for the parser.
        """
        results = self._get_all_ocr_results(image, n_strips=4)

        if not results:
            print("  EasyOCR returned no results.")
            return ""

        # ----------------------------------------------------------------
        # Build token list:  (y_c, x_c, text, conf)
        # ----------------------------------------------------------------
        tokens = []
        for (bbox, text, conf) in results:
            if conf < 0.05 or not text.strip():
                continue
            xs = [pt[0] for pt in bbox]
            ys = [pt[1] for pt in bbox]
            x_c = (min(xs) + max(xs)) / 2
            y_c = (min(ys) + max(ys)) / 2
            tokens.append((y_c, x_c, text.strip(), conf))

        if not tokens:
            return ""

        tokens.sort(key=lambda t: t[0])   # sort by Y

        img_width  = image.shape[1] if hasattr(image, 'shape') else 1000

        # ----------------------------------------------------------------
        # Step 1: Detect column X-boundaries from header row
        # ----------------------------------------------------------------
        num_x_mid   = img_width * 0.06
        white_x_mid = img_width * 0.38
        black_x_mid = img_width * 0.72

        for tok in tokens[:30]:   # header is near the top
            txt_lower = tok[2].lower().strip('#').strip()
            if txt_lower == 'white':
                white_x_mid = tok[1]
            elif txt_lower == 'black':
                black_x_mid = tok[1]
            elif txt_lower == '#':
                num_x_mid = tok[1]

        num_white_boundary   = (num_x_mid + white_x_mid) / 2
        white_black_boundary = (white_x_mid + black_x_mid) / 2

        print(f"  Column X-boundaries: #<{num_white_boundary:.0f} | "
              f"WHITE<{white_black_boundary:.0f} | BLACK>")

        # ----------------------------------------------------------------
        # Step 2: Cluster tokens into rows by Y centroid
        # ----------------------------------------------------------------
        heights = []
        for (_, _, text, conf) in tokens:
            pass   # use global median
        # Estimate row height from consecutive Y gaps
        ys_sorted = sorted(t[0] for t in tokens)
        gaps = [ys_sorted[i+1] - ys_sorted[i]
                for i in range(len(ys_sorted)-1)
                if ys_sorted[i+1] - ys_sorted[i] > 2]
        if gaps:
            gaps.sort()
            typical_gap = gaps[len(gaps)//2]
        else:
            typical_gap = 20
        row_tol = max(10, typical_gap * 0.6)

        rows: List[List[tuple]] = []
        current_row: List[tuple] = [tokens[0]]
        for tok in tokens[1:]:
            if abs(tok[0] - current_row[-1][0]) <= row_tol:
                current_row.append(tok)
            else:
                rows.append(sorted(current_row, key=lambda t: t[1]))
                current_row = [tok]
        rows.append(sorted(current_row, key=lambda t: t[1]))

        # ----------------------------------------------------------------
        # Step 3: Assign each token to a column and build "N. w b" lines
        # ----------------------------------------------------------------
        header_noise = {'event','round','board','section','opening','control',
                        'time','ttme','white','black','result','draw','#',
                        'rating','ratng','ratins','ratin','signature','won',
                        'abhyuday','mitas','abhyudoy','abhyudey',
                        'draw','signature','isection','opening'}

        line_strings = []
        for row in rows:
            all_text = " ".join(t[2] for t in row).lower()
            if any(kw in all_text for kw in header_noise):
                continue

            num_tok, white_tok, black_tok = [], [], []
            for tok in row:
                x_c = tok[1]
                txt = tok[2]
                if x_c < num_white_boundary:
                    num_tok.append(txt)
                elif x_c < white_black_boundary:
                    white_tok.append(txt)
                else:
                    black_tok.append(txt)

            num_str   = " ".join(num_tok).strip().strip('.,;:')
            white_str = " ".join(white_tok).strip()
            black_str = " ".join(black_tok).strip()

            if num_str.isdigit():
                parts = [f"{num_str}."]
                if white_str:
                    parts.append(white_str)
                if black_str:
                    parts.append(black_str)
                line_strings.append(" ".join(parts))

        text = "\n".join(line_strings)
        print(f"  Column-aware extraction: {len(line_strings)} move rows")
        print(f"  Reconstructed notation:\n{text}")
        return text



    def _extract_tesseract(self, image) -> str:
        """Extract text using Tesseract (requires tesseract executable)."""
        if not TESSERACT_AVAILABLE:
            print("  Tesseract Python package not available.")
            return ""
        try:
            custom_config = r'--oem 3 --psm 6'
            text = pytesseract.image_to_string(image, config=custom_config)
            print(f"  Tesseract extracted {len(text)} characters")
            return text
        except Exception as e:
            print(f"  Tesseract error: {e}")
            return ""


class NotationParser:
    """Parses chess notation text into structured move data"""
    
    def __init__(self, notation_type: NotationType = NotationType.ALGEBRAIC):
        """
        Initialize the notation parser
        
        Args:
            notation_type: Type of chess notation to parse
        """
        self.notation_type = notation_type
    
    def parse_moves(self, text: str) -> List[ChessMove]:
        """
        Parse chess notation text into a list of moves.

        Handles two input formats:
        1. Standard PGN / linear text:   "1. e4 e5  2. Nf3 Nc6 ..."
        2. Chess scoresheet rows:        "1 d4 d5"  or  "1 d4 d5 31 Qd2 Qe7"
                                         (number without dot, 0-2 SAN tokens)

        Args:
            text: Raw text (spatially reconstructed from OCR rows)

        Returns:
            List of ChessMove objects, one per move number, sorted ascending.
        """
        print(f"Parsing moves using {self.notation_type.value} notation...")

        move_dict: Dict[int, ChessMove] = {}

        # ----------------------------------------------------------------
        # Regex helpers
        # ----------------------------------------------------------------
        san_token = r'(?:O-O-O|O-O|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?)'
        san_re    = re.compile(r'^' + san_token + r'$', re.IGNORECASE)
        num_re    = re.compile(r'^\d{1,3}[.,]?$')   # bare number like "1", "31.", "9:"

        # ----------------------------------------------------------------
        # Pass 1 – standard PGN pattern  "N. white [black]"
        # ----------------------------------------------------------------
        pgn_pattern = (
            r'(\d+)\.+\s*'
            r'(' + san_token + r')'
            r'(?:\s+(' + san_token + r'))?'
        )
        pgn_orphan = r'(\d+)\.{2,3}\s*(' + san_token + r')'

        for match in re.finditer(pgn_pattern, text):
            n = int(match.group(1))
            wm = self.normalize_move(match.group(2))
            bm = self.normalize_move(match.group(3)) if match.group(3) else None
            if wm:
                move_dict[n] = ChessMove(move_number=n, white_move=wm,
                                         black_move=bm, confidence=0.9)
        for match in re.finditer(pgn_orphan, text):
            n  = int(match.group(1))
            bm = self.normalize_move(match.group(2))
            if n in move_dict and move_dict[n].black_move is None:
                move_dict[n].black_move = bm
            elif n not in move_dict and bm:
                move_dict[n] = ChessMove(move_number=n, white_move="?",
                                         black_move=bm, confidence=0.7)

        # ----------------------------------------------------------------
        # Pass 2 – scoresheet row parser
        #   Each OCR "line" may look like:
        #     "1 d4 d5"             → move 1: white=d4, black=d5
        #     "1 d4 d5 31 Qd1 Nd7"  → move 1 AND move 31
        #     "3, Nf3 Rex 33"       → move 3 (Rex=OCR noise) AND empty move 33
        #   Strategy: tokenise each line; whenever we see a pure number treat
        #   it as a new move-number and collect up to 2 SAN tokens that follow.
        # ----------------------------------------------------------------
        for line in text.splitlines():
            tokens = line.split()
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                stripped = tok.rstrip('.,;:')
                if stripped.isdigit():
                    move_num = int(stripped)
                    # Already captured by Pass 1 with high confidence → skip
                    if move_num in move_dict and move_dict[move_num].white_move != "?":
                        i += 1
                        continue
                    # Collect subsequent SAN tokens
                    san_tokens: List[str] = []
                    j = i + 1
                    while j < len(tokens) and len(san_tokens) < 2:
                        candidate = self.normalize_move(tokens[j])
                        if san_re.match(candidate) and candidate:
                            san_tokens.append(candidate)
                            j += 1
                        elif num_re.match(tokens[j]):
                            break  # next move-number
                        else:
                            j += 1  # skip noise
                    if not san_tokens:
                        i += 1
                        continue
                    wm = san_tokens[0]
                    bm = san_tokens[1] if len(san_tokens) > 1 else None
                    existing = move_dict.get(move_num)
                    if existing is None:
                        move_dict[move_num] = ChessMove(
                            move_number=move_num,
                            white_move=wm, black_move=bm, confidence=0.75
                        )
                    else:
                        # Fill in missing black move
                        if existing.black_move is None and bm:
                            existing.black_move = bm
                    i = j
                else:
                    i += 1

        # ----------------------------------------------------------------
        # Filter out empty / header moves and sort
        # ----------------------------------------------------------------
        valid = {n: m for n, m in move_dict.items()
                 if m.white_move and m.white_move not in ("-", "?")}
        moves = [valid[k] for k in sorted(valid)]

        print(f"  Parsed {len(moves)} move pairs")
        if moves:
            for m in moves[:5]:
                bstr = f" {m.black_move}" if m.black_move else ""
                print(f"    {m.move_number}. {m.white_move}{bstr}")
            if len(moves) > 5:
                print(f"    ... ({len(moves) - 5} more)")
        return moves

    
    def validate_move(self, move: str, position: Optional[str] = None) -> bool:
        """
        Validate if a move is legal in chess
        
        Args:
            move: Move string to validate
            position: Optional FEN string representing current position
            
        Returns:
            True if move is valid, False otherwise
        """
        if not CHESS_AVAILABLE:
            # Can't validate without python-chess, assume valid
            return True
        
        try:
            board = chess.Board(position) if position else chess.Board()
            # Try to parse the move in SAN (Standard Algebraic Notation)
            board.parse_san(move)
            return True
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError):
            return False
    
    def normalize_move(self, move: str) -> str:
        """
        Normalize a handwritten/OCR'd move to standard algebraic notation.

        Handles common handwriting OCR confusions:
        - 'Q' written like 'q' or '0'
        - 'x' written with various symbols
        - castling variants (O-O, 0-0, etc.)
        - spaces within a token (e.g. "e 4" → "e4")
        - stray punctuation

        Args:
            move: Move string from OCR

        Returns:
            Normalized move string in SAN format
        """
        # Remove leading/trailing whitespace and stray periods
        move = move.strip()

        # Remove internal spaces (e.g. "e 4" → "e4", "N f3" → "Nf3")
        move = re.sub(r'(?<=[a-zA-Z])\s+(?=[a-h1-8xX])', '', move)
        move = re.sub(r'(?<=[a-h])\s+(?=[1-8])', '', move)

        # Remove stray delimiter characters at start/end
        move = move.strip('.,;:()')

        # --- Handle castling FIRST (before any substitution) ---
        castling_queenside = {'O-O-O','0-0-0','OOO','000','o-o-o','ooo',
                               'O-0-O','0-O-0','O-O-0','0-0-O'}
        castling_kingside  = {'O-O','0-0','OO','00','o-o','oo',
                               'O-0','0-O'}
        # Also handle written as "O - O - O" with spaces
        compact = move.replace(' ', '').replace('-', '')
        if compact.upper() in {'OOO','000'}:
            return 'O-O-O'
        if compact.upper() in {'OO','00'}:
            return 'O-O'
        if move in castling_queenside:
            return 'O-O-O'
        if move in castling_kingside:
            return 'O-O'

        # --- Common handwriting OCR character fixes ---

        # Piece letters: uppercase them
        # 'q' at start → 'Q'  (queen often written lowercase)
        move = re.sub(r'^q', 'Q', move)
        # 'b' at start followed by a file letter → likely 'B' (bishop)
        move = re.sub(r'^b(?=[a-hA-H1-8x])', 'B', move)
        # 'r' at start → 'R'
        move = re.sub(r'^r(?=[a-hA-H1-8x])', 'R', move)
        # 'n' at start → 'N'
        move = re.sub(r'^n(?=[a-hA-H1-8x])', 'N', move)
        # 'k' at start → 'K'
        move = re.sub(r'^k(?=[a-hA-H1-8x])', 'K', move)

        # Capture symbol: normalize various OCR variants of 'x'
        move = re.sub(r'[Xx*×✕]', 'x', move)

        # Rank digits: '0' (zero) appearing as rank after a file letter
        move = re.sub(r'([a-hA-H])0', lambda m: m.group(1) + 'o', move)
        # Actually we want the opposite: 'o' after a file → digit confusion
        # e.g. "eo" probably means "e0"?  Ranks are 1-8, not 0. Leave alone.

        # Lowercase file letters (they should be lowercase already)
        # Piece letter capitalisation check: [KQRBN] then file+rank
        move = re.sub(r'^([kqrbn])([a-h][1-8])', 
                      lambda m: m.group(1).upper() + m.group(2), move)

        # Check/checkmate markers: keep as-is (+, #)
        # Promotion: '=' should stay (e.g. e8=Q)

        # Remove any remaining stray non-chess characters but keep +#=x
        move = re.sub(r'[^a-zA-Z0-9+#=x\-]', '', move)

        return move


class UserVerificationInterface:
    """Provides interface for user to verify and correct extracted moves"""
    
    def __init__(self):
        """Initialize the verification interface"""
        self.corrections = []
    
    def display_move(self, move: ChessMove, original_image=None):
        """
        Display a move for user verification
        
        Args:
            move: ChessMove object to display
            original_image: Optional image showing the original notation
        """
        print(f"\nMove {move.move_number}:")
        print(f"  White: {move.white_move}")
        if move.black_move:
            print(f"  Black: {move.black_move}")
        print(f"  Confidence: {move.confidence:.2%}")
    
    def request_correction(self, move: ChessMove) -> ChessMove:
        """
        Request user to verify or correct a move
        
        Args:
            move: ChessMove object to verify
            
        Returns:
            Corrected ChessMove object
        """
        self.display_move(move)
        
        # TODO: Implement interactive correction interface
        # For now, just return the original move
        return move
    
    def batch_verify(self, moves: List[ChessMove]) -> List[ChessMove]:
        """
        Allow user to verify multiple moves at once
        
        Args:
            moves: List of ChessMove objects to verify
            
        Returns:
            List of verified/corrected ChessMove objects
        """
        verified_moves = []
        for move in moves:
            verified_move = self.request_correction(move)
            verified_moves.append(verified_move)
        
        return verified_moves


class PGNGenerator:
    """Generates PGN files from verified chess moves"""
    
    def __init__(self):
        """Initialize the PGN generator"""
        pass
    
    def generate_pgn(self, metadata: GameMetadata, moves: List[ChessMove]) -> str:
        """
        Generate a PGN string from game metadata and moves
        
        Args:
            metadata: GameMetadata object with header information
            moves: List of ChessMove objects
            
        Returns:
            Complete PGN string
        """
        pgn_parts = [metadata.to_pgn_headers(), ""]
        
        # Generate move text
        move_text = []
        for move in moves:
            move_str = f"{move.move_number}. {move.white_move}"
            if move.black_move:
                move_str += f" {move.black_move}"
            move_text.append(move_str)
        
        # Format moves with line breaks (80 character limit per PGN standard)
        pgn_parts.append(" ".join(move_text))
        pgn_parts.append(f" {metadata.result}")
        
        return "\n".join(pgn_parts)
    
    def save_to_file(self, pgn: str, output_path: str):
        """
        Save PGN string to a file
        
        Args:
            pgn: PGN string to save
            output_path: Path to output file
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(pgn)
        print(f"PGN saved to: {output_path}")


class ChessNotationConverter:
    """Main converter class that orchestrates the conversion process"""
    
    def __init__(self, notation_type: NotationType = NotationType.ALGEBRAIC):
        """
        Initialize the chess notation converter
        
        Args:
            notation_type: Type of chess notation to expect
        """
        self.preprocessor = ImagePreprocessor()
        self.ocr_engine = OCREngine()
        self.parser = NotationParser(notation_type)
        self.verifier = UserVerificationInterface()
        self.pgn_generator = PGNGenerator()
    
    def convert_image_to_pgn(self, image_path: str, output_path: str, 
                            metadata: Optional[GameMetadata] = None) -> str:
        """
        Complete conversion pipeline from image to PGN file
        
        Args:
            image_path: Path to input image
            output_path: Path to output PGN file
            metadata: Optional game metadata
            
        Returns:
            Generated PGN string
        """
        print(f"\n{'='*60}")
        print("Chess Notation to PGN Conversion")
        print(f"{'='*60}\n")
        
        # Step 1: Load and preprocess image
        print("Step 1: Loading and preprocessing image...")
        image = self.preprocessor.load_image(image_path)
        enhanced_image = self.preprocessor.enhance_image(image)
        
        # Step 2: Extract text using OCR
        print("\nStep 2: Extracting text using OCR...")
        text = self.ocr_engine.extract_text(enhanced_image)
        
        # Step 3: Parse chess notation
        print("\nStep 3: Parsing chess notation...")
        moves = self.parser.parse_moves(text)
        
        # Step 4: User verification
        print("\nStep 4: User verification...")
        verified_moves = self.verifier.batch_verify(moves)
        
        # Step 5: Generate PGN
        print("\nStep 5: Generating PGN...")
        if metadata is None:
            metadata = GameMetadata()
        
        pgn = self.pgn_generator.generate_pgn(metadata, verified_moves)
        
        # Step 6: Save to file
        print("\nStep 6: Saving PGN file...")
        self.pgn_generator.save_to_file(pgn, output_path)
        
        print(f"\n{'='*60}")
        print("Conversion complete!")
        print(f"{'='*60}\n")
        
        return pgn


def main():
    """Main entry point for the application"""
    print("Chess Notation to PGN Converter")
    print("================================\n")
    
    # Example usage
    converter = ChessNotationConverter(NotationType.ALGEBRAIC)
    
    # TODO: Replace with actual image path
    image_path = "C:/Users/anirb/OneDrive/Documents/Game2PGN/Game2PGN/notation.jpg"  # File is in the same directory as this script
    output_path = "output_game.pgn"
    
    # Create sample metadata
    metadata = GameMetadata(
        event="Training Game",
        site="Local Club",
        date="2026.02.04",
        white="Player 1",
        black="Player 2",
        result="*"
    )
    
    # Uncomment when ready to test with actual images
    pgn = converter.convert_image_to_pgn(image_path, output_path, metadata)
    print(f"\nGenerated PGN:\n{pgn}")
    
    print("Note: This is a framework. Implement TODO sections with:")
    print("  - PIL/Pillow or OpenCV for image processing")
    print("  - Tesseract OCR or EasyOCR for text extraction")
    print("  - python-chess library for move validation")
    print("  - tkinter or PyQt for GUI verification interface")


if __name__ == "__main__":
    main()
