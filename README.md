# Tweet Availability Checker
*A Streamlit + Python tool for validating whether tweets in a CSV/XLSX file are still available.*

## Overview
Tweet Availability Checker is a small utility application that accepts a CSV or Excel file containing tweet URLs and returns a new file with metadata about each tweet’s availability.

It was originally built to support content research, archiving, and link-integrity checking, but is currently **inactive/defective** due to Twitter API and oEmbed pipelining bugs.

The tool includes both:
- A **Streamlit web interface** (`app.py`)
- A **standalone Python script** (`check_tweet_links.py`) for CLI batch processing

## Virtually Hosted on Streamlit:
https://waybackurlchecking-kz4yewmwyy4jah7bahksbm.streamlit.app/

## Features

### Streamlit App
- Upload `.csv` or `.xlsx` files containing tweet URLs  
- Automatically extracts tweet IDs  
- Checks each URL using:
  - Twitter's legacy oEmbed endpoint  
  - A backup direct HTTP fetch (generic user-agent)  
- Displays:
  - Number of available tweets  
  - Number of unavailable tweets  
- Exports CSV and XLSX files containing:
  - `original_url`
  - `tweet_id`
  - `status_code`
  - `detail`
  - `checked_url`

### CLI Script
Run tweet checks from the command line:

```bash
python check_tweet_links.py input.xlsx
```

Outputs a new file named:

```
input_links_checked.xlsx
```

## Current Limitations
This project is currently **inactive/defective** because:
- Twitter’s oEmbed endpoint frequently fails, even for valid tweets  
- Twitter/X often blocks unauthenticated scraping requests  
- The fallback request method is not reliably accurate  

As a result, availability checks may be incomplete or incorrect.  
A future version will likely require authenticated Twitter API v2 calls or a browser-automation approach.

## Tech Stack
- Python 3.9+  
- Streamlit  
- pandas  
- requests  
- openpyxl  

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/DGA-Research/<your-repo>
cd tweet-availability-checker
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

## Running the Streamlit App
```bash
streamlit run app.py
```

Open the displayed URL (typically `http://localhost:8501`).

## Running the CLI Script
```bash
python check_tweet_links.py path/to/input.xlsx
```

Outputs:

```
path/to/input_links_checked.xlsx
```

## File Structure
```
.
├── app.py                  # Streamlit user interface
├── check_tweet_links.py    # Core tweet-checking logic (CLI tool)
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

## How It Works
The tweet checker uses two methods:

1. **oEmbed Check**  
   Calls `https://publish.twitter.com/oembed?url=<tweet_url>` and interprets response codes.

2. **Fallback HTTP Fetch**  
   Requests the cleaned tweet URL with a browser-like user-agent and interprets:
   - 200 (available)  
   - 302 (redirect/protected)  
   - 404/410 (removed)  
   - Other error statuses  

Both checks are combined into the final output rows consumed by the UI and CLI.
**Helen Smith (@littlemissbway42)**  
DGA Research
