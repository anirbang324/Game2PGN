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
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    print("Warning: pytesseract not available")

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
        Apply preprocessing techniques to improve OCR accuracy
        
        Techniques include:
        - Grayscale conversion
        - Noise reduction
        - Contrast enhancement
        - Binarization (thresholding)
        - Deskewing (rotation correction)
        
        Args:
            image: Input image object
            
        Returns:
            Enhanced image object
        """
        print("Enhancing image for OCR...")
        
        if not CV2_AVAILABLE:
            print("  Skipping enhancement (OpenCV not available)")
            return image
        
        # Convert to grayscale
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        
        # Apply denoising
        denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        
        # Apply adaptive thresholding for better text extraction
        binary = cv2.adaptiveThreshold(
            denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY, 11, 2
        )
        
        # Increase contrast
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(binary)
        
        print("  Applied: grayscale, denoising, thresholding, contrast enhancement")
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
    """Handles optical character recognition of chess notation"""
    
    def __init__(self, engine: str = "tesseract"):
        """
        Initialize the OCR engine
        
        Args:
            engine: OCR engine to use ('tesseract', 'easyocr', etc.)
        """
        self.engine = engine
        print(f"Initializing OCR engine: {engine}")
    
    def extract_text(self, image, region: Optional[Tuple[int, int, int, int]] = None) -> str:
        """
        Extract text from an image or image region
        
        Args:
            image: Input image object
            region: Optional bounding box (x, y, width, height) to extract from
            
        Returns:
            Extracted text string
        """
        print("Extracting text from image...")
        
        if not TESSERACT_AVAILABLE:
            print("  Warning: Tesseract not available, returning empty string")
            return ""
        
        try:
            # Extract region if specified
            if region:
                x, y, w, h = region
                if CV2_AVAILABLE and isinstance(image, np.ndarray):
                    image = image[y:y+h, x:x+w]
                elif PIL_AVAILABLE:
                    image = image.crop((x, y, x+w, y+h))
            
            # Configure Tesseract for better accuracy with chess notation
            custom_config = r'--oem 3 --psm 6'
            
            # Extract text
            if CV2_AVAILABLE and isinstance(image, np.ndarray):
                text = pytesseract.image_to_string(image, config=custom_config)
            else:
                text = pytesseract.image_to_string(image, config=custom_config)
            
            print(f"  Extracted {len(text)} characters")
            return text
        
        except Exception as e:
            print(f"  Error during OCR: {e}")
            return ""
    
    def get_confidence_scores(self, image) -> Dict[str, float]:
        """
        Get confidence scores for extracted text
        
        Args:
            image: Input image object
            
        Returns:
            Dictionary mapping text to confidence scores
        """
        if not TESSERACT_AVAILABLE:
            return {}
        
        try:
            # Get detailed OCR data with confidence scores
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            
            confidence_map = {}
            for i, text in enumerate(data['text']):
                if text.strip():
                    conf = float(data['conf'][i]) / 100.0  # Convert to 0-1 range
                    confidence_map[text] = conf
            
            return confidence_map
        except Exception as e:
            print(f"  Error getting confidence scores: {e}")
            return {}


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
        Parse chess notation text into a list of moves
        
        Args:
            text: Raw text containing chess moves
            
        Returns:
            List of ChessMove objects
        """
        moves = []
        print(f"Parsing moves using {self.notation_type.value} notation...")
        
        # Pattern to match chess moves: move number, white move, optional black move
        # Examples: "1. e4 e5", "2. Nf3 Nc6", "3. Bb5"
        move_pattern = r'(\d+)\.\s*([A-Za-z0-9+#=\-x]+)(?:\s+([A-Za-z0-9+#=\-x]+))?'
        
        matches = re.findall(move_pattern, text)
        
        for match in matches:
            move_num = int(match[0])
            white_move = match[1].strip()
            black_move = match[2].strip() if match[2] else None
            
            # Normalize moves
            white_move = self.normalize_move(white_move)
            if black_move:
                black_move = self.normalize_move(black_move)
            
            chess_move = ChessMove(
                move_number=move_num,
                white_move=white_move,
                black_move=black_move,
                confidence=0.9  # Default confidence, can be updated with OCR scores
            )
            moves.append(chess_move)
        
        print(f"  Parsed {len(moves)} move pairs")
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
        Normalize a move to standard algebraic notation
        
        Args:
            move: Move string in any supported format
            
        Returns:
            Normalized move string
        """
        # Remove extra whitespace
        move = move.strip()
        
        # Remove common OCR errors
        move = move.replace('O', '0')  # Sometimes O is misread as 0
        move = move.replace('o', '0')
        
        # Handle castling notation
        if move in ['0-0', '00', 'O-O']:
            move = 'O-O'
        elif move in ['0-0-0', '000', 'O-O-O']:
            move = 'O-O-O'
        
        # Remove periods that might have been captured
        move = move.replace('.', '')
        
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
