"""
Debug script to test OCR functionality and check what's available
"""

import sys
import os

print("="*60)
print("Chess Notation Converter - Dependency Check")
print("="*60)

# Check PIL/Pillow
try:
    from PIL import Image
    print("✓ PIL/Pillow: Available")
except ImportError:
    print("✗ PIL/Pillow: NOT available")

# Check OpenCV
try:
    import cv2
    import numpy as np
    print(f"✓ OpenCV: Available (version {cv2.__version__})")
except ImportError:
    print("✗ OpenCV: NOT available")

# Check pytesseract
try:
    import pytesseract
    print("✓ pytesseract: Available")
    
    # Try to get Tesseract version
    try:
        version = pytesseract.get_tesseract_version()
        print(f"  ✓ Tesseract executable: Found (version {version})")
    except Exception as e:
        print(f"  ✗ Tesseract executable: NOT found")
        print(f"    Error: {e}")
        print(f"    Please install Tesseract from: https://github.com/UB-Mannheim/tesseract/wiki")
except ImportError:
    print("✗ pytesseract: NOT available")

# Check EasyOCR
try:
    import easyocr
    print("✓ EasyOCR: Available")
except ImportError:
    print("✗ EasyOCR: NOT available (optional)")

# Check python-chess
try:
    import chess
    print("✓ python-chess: Available")
except ImportError:
    print("✗ python-chess: NOT available")

print("="*60)
print("\nTesting image loading...")

image_path = "notation.jpg"
if os.path.exists(image_path):
    print(f"✓ Found image: {image_path}")
    
    # Try to load with OpenCV
    try:
        import cv2
        img = cv2.imread(image_path)
        if img is not None:
            print(f"  ✓ Loaded with OpenCV: {img.shape}")
        else:
            print(f"  ✗ Failed to load with OpenCV")
    except:
        pass
    
    # Try to load with PIL
    try:
        from PIL import Image
        img = Image.open(image_path)
        print(f"  ✓ Loaded with PIL: {img.size}")
    except:
        pass
else:
    print(f"✗ Image not found: {image_path}")

print("="*60)
