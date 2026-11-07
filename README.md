# CloudQA Task 2: Autonomous AI QA Agent

This is a Python-based AI tool that takes plain-English test cases and executes them autonomously in a browser. It is not hardcoded to any specific website or DOM structure.

## Tech Stack
- **Python & Playwright:** For headless browser control and DOM extraction.
- **Google Gemini (gemini-3.8-flash):** For reasoning and action decisions.
- **Pydantic:** To strictly enforce the JSON output schema from the LLM.

## Setup & Execution
1. Clone the repository and navigate to the directory.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   pip install playwright google-genai pydantic python-dotenv
   playwright install chromium