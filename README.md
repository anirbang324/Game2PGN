# ♟️ Chess Notation Converter

Convert **handwritten chess score sheets** into PGN or CSV using Google Gemini AI — **completely free**.

## Features
- 📷 Upload a photo or scan of any handwritten score sheet
- 🤖 Gemini reads the handwriting and extracts moves automatically
- ✅ Moves are validated against real chess rules
- 📥 Export as **PGN** (standard chess format) or **CSV** (spreadsheet-friendly)
- 💸 **100% free** — uses Google Gemini's free API tier, no credit card needed

## Setup

### 1. Get a free Gemini API Key
1. Visit https://aistudio.google.com/app/apikey
2. Sign in with your Google account
3. Click **Create API Key**
4. Copy the key (starts with `AIza...`)

### 2. Install dependencies
```
pip install -r requirements.txt
```

### 3. Run the app
```
streamlit run app.py
```

The app opens at http://localhost:8501
Paste your API key into the **sidebar** when prompted.

## Usage
1. Paste your Gemini API key in the left sidebar
2. Upload a photo of your score sheet (JPG, PNG, or WebP)
3. Click Extract & Convert
4. Review the extracted moves
5. Download PGN, CSV, or both!

## Free tier limits (more than enough for a school project)
- 1,500 requests per day
- 15 requests per minute
- No credit card required
