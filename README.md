# Task 2: Plain-English Test Runner

Give it a test case written in plain English. It opens a real browser, carries the test out step by step, and reports **PASS**, **FAIL** or **ERROR**. Nothing in the code is specific to one website.

## Setup

You need Python 3.10+ and a Gemini API key.

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env        # then put your key in .env
```

## Run

```bash
# one test
python agent.py --url https://phptravels.com/demo --test "Verify that the login button is visible on the page"

# with test data (the model is told to use it exactly)
python agent.py --url <site> --test "Log in as a customer and verify the dashboard is shown" --data email=you@example.com password=secret

# several tests from a file
python agent.py --tests-file tests.example.txt --url <default site>

# other options
#   --headless      hide the browser      --vision      also send a screenshot each step
#   --max-steps 15  step limit            --model NAME  choose the Gemini model
```

Exit code: `0` = all PASS, `1` = at least one FAIL, `2` = at least one ERROR (no verdict, model failure, stuck).
Each run saves a screenshot and a JSON log of the steps in `reports/`.

## How it works

Each step the program:
1. **Reads the page**: URL, title, visible text, and a numbered list of every visible interactive element (links, buttons, inputs, dropdowns), including iframes and open shadow DOM.
2. **Asks the model for one action** as JSON: `click`, `type`, `select`, `press`, `navigate`, `wait` or `done`. The model also sees the test case, the test data and what it already did.
3. **Runs the action** with Playwright. If it fails, the error is shown to the model so it can try something else.
4. **Checks the verdict.** For a PASS the model must quote evidence from the page, and the program checks that the quote is really in the page text, URL or title. An invented PASS is rejected.

New tabs and pop-ups are followed automatically. Passwords are never printed.

