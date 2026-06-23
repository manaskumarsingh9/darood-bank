# Darood Bank App

A Python application for extracting and counting religious chants from WhatsApp chat exports using Google's Gemini AI.

## Features

- **Automated Extraction:** Uses AI to identify chant names and counts from unstructured text.
- **Normalization:** Automatically maps various spellings (e.g., "Darood", "Durood") to a standard list.
- **Verification:** Includes a secondary AI verification step to ensure data accuracy.
- **Reporting:** Generates detailed CSV and Text summaries, including daily breakdowns.

## Prerequisites

- **Python 3.8+** installed on your system.
- A **Google Cloud Project** with the Gemini API enabled.
- An **API Key** for Google Gemini.

## Installation

1.  **Clone or Download** this repository to your local machine.
2.  Open a terminal/command prompt in the project folder.
3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

1.  **Create a `.env` file:**
    - Copy the example file:
      - **Windows:** `copy .env.example .env`
      - **Mac/Linux:** `cp .env.example .env`
    - Or simply create a new file named `.env` in the project root.

2.  **Add your API Key:**
    - Open the `.env` file in a text editor.
    - Paste your Google API Key:
      ```env
      GOOGLE_API_KEY=your_actual_api_key_here
      ```

## Usage

You can run the application in two modes:

### 1. Batch Mode (Default)
Processes all `.txt` files found in the `inputs/` folder.

```bash
python main.py
```

- Place your WhatsApp export files (text format) inside the `inputs/` folder.
- After processing, files are automatically moved to `inputs/processed/`.

### 2. Single File Mode
Process a specific file by providing its path.

```bash
python main.py inputs/my_chat_export.txt
```

## Input Format

The tool is designed to work with WhatsApp chat exports or similar text files.
- It looks for lines starting with dates (e.g., `16/02/2026`) or timestamps (e.g., `[12:00 pm, 16/02/26]`).
- It extracts counts like "100 darood", "5 tasbih astagfar", etc.

## Outputs

Results are saved in the `outputs/` directory, organized by timestamp:

`outputs/YYYY-MM-DD_HH-MM-SS/`
- **`summary.txt`**: A simple list of totals (e.g., `DAROOD = 500+100`).
- **`summary.csv`**: A spreadsheet row with total counts for each chant.
- **`daily_breakdown.csv`**: A day-by-day table of counts.

## Troubleshooting

- **"No input files found"**: Make sure your `.txt` files are inside the `inputs/` folder.
- **API Errors**: Check that your `GOOGLE_API_KEY` in `.env` is correct and has active quota.
