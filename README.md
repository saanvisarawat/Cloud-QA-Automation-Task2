# CloudQA Task 2: Plain-English Test Runner

Give this tool a test case written in plain English and a starting URL. It opens a real browser, carries the test out step by step, and reports **PASS**, **FAIL** or **ERROR**.

Nothing in the code is specific to one website. There are no fixed selectors: at every step the agent reads the page as it currently looks and a Gemini model decides what to do next.

## Setup

You need Python 3.10 or newer and a Gemini API key (free keys from Google AI Studio work).

```bash
git clone https://github.com/saanvisarawat/Cloud-QA-Automation-Task2.git
cd Cloud-QA-Automation-Task2
pip install -r requirements.txt
playwright install chromium
```

Create your `.env` file by copying the tessmplate (`copy .env.example .env` on Windows, `cp .env.example .env` on Mac/Linux) and put your key in it:

```
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=the_model_name_you_want_to_use
```

`GEMINI_MODEL` is optional, but model names change over time. If you get a "model not found" error, look up a current model name in Google AI Studio and set it here, or pass `--model NAME` on the command line. `.env` is in `.gitignore`, so your key is never committed.

## Running it

```bash
# One test
python agent.py --url https://phptravels.com/demo --test "Verify that the login button is visible on the page"

# Test data the agent should use exactly as given
python agent.py --url <site> --test "Log in as a customer and verify the dashboard is shown" --data email=you@example.com password=yourpassword

# Same code, a completely different site
python agent.py --url https://the-internet.herokuapp.com/login --test "Log in and verify the secure area message appears" --data username=tomsmith password=SuperSecretPassword!

# Several tests from a file (see tests.example.txt)
python agent.py --tests-file tests.example.txt --url <default site>
```

Options:

| Option | What it does |
|---|---|
| `--url` | Page to start on |
| `--test` | The test case in plain English |
| `--tests-file` | A file with one test per line, either `test text` or `url \|\| test text` |
| `--data key=value ...` | Test data such as email or password. The agent is told to use these exactly |
| `--headless` | Hide the browser window |
| `--vision` | Also send a screenshot to the model at each step |
| `--max-steps N` | Step limit per test (default 15) |
| `--model NAME` | Gemini model to use |
| `--report-dir DIR` | Where to save reports (default `reports`) |

The exit code is `0` when every test passes, `1` if at least one fails, and `2` if at least one ends in an error (no verdict, model failure, or the agent got stuck).

## What the output looks like

For each test the console shows the steps taken and then the result:

```
TEST: <your test case>
URL:  <start url>
  step 1: click id=7 value=None - <why the model chose this>
  step 2: done ...
RESULT: PASS - <reason> (evidence: "<text that was on the page>")
```

When you run several tests, a summary table is printed at the end. Every test also saves a screenshot and a JSON file (status, reason, evidence, steps, seconds) in `reports/`.

## How it works

Each step is the same loop:

1. **Read the page.** The program collects the URL, the title, the visible text, and a numbered list of every visible interactive element (links, buttons, inputs, dropdowns). It looks inside iframes and open shadow DOM too. Password values are hidden.
2. **Ask the model for one action.** The model sees the test case, the test data, what it has already done, and the current page. It answers with a single JSON action: `click`, `type`, `select`, `press`, `navigate`, `wait` or `done`. The answer is validated before anything runs.
3. **Do it with Playwright.** If the action fails (for example, the element disappeared), the error is passed back to the model so it can try something else.
4. **Check the verdict.** When the model says `done`, a PASS is only accepted if the evidence it quotes really appears in the page text, URL or title. If the quote can't be found, the PASS is rejected.

New tabs and pop-ups are followed automatically, so a login link that opens in a new tab doesn't break the run. If the agent repeats the same action without making progress, or runs out of steps, the test ends as ERROR instead of looping.

## Testing

I ran these tests. Results are copied from the saved reports in `reports/`.

| Site | Test | Result | Notes |
|---|---|---|---|
| phptravels.com/demo | Verify Customer Login link is visible | PASS | Instantly read from the main DOM structure. |
| phptravels.com/demo | Click Customer Login and verify form | PASS | Successfully detected the new tab opening and switched context. |
| phptravels.com/demo | Find and open chat widget | ERROR | The chat widget is embedded in a cross-origin iframe/closed shadow DOM, blocking interaction. |
| the-internet.herokuapp.com | Log in with valid credentials | PASS | Correctly mapped the inputs, typed the data, and verified the success banner. |
| the-internet.herokuapp.com | Log in with invalid credentials | PASS | Submitted the form and verified the red invalid username banner appeared. |

The phptravels chat widget test ended in an ERROR because the widget is injected via a cross-origin third-party script, preventing the Playwright DOM walker from accessing its internal structure. This highlights the limitation of DOM-based reasoning against heavily isolated iframes.

## Limitations

- **It is a language model making decisions.** Results can vary between runs, and it can misread a page. Run a test twice if the result looks odd.
- **Speed and cost.** Every step is a model call, so it is much slower than a normal scripted test. A test takes **roughly 15 to 25 seconds depending on the number of reasoning steps** in my runs. Free-tier rate limits can slow or stop long tests.
- **Anti-bot protection.** Sites that use CAPTCHA, one-time codes or bot detection can block the browser, and the test will end as ERROR or FAIL.
- **Hard interfaces.** Drag and drop, canvas-based pages, and custom widgets with no readable text may not work.
- **Closed shadow DOM** can't be read by any script, so elements inside it are invisible to the agent.
- **No pixel-level checks.** It reasons mainly over page text and structure. "Is this button red" is outside its scope unless you use `--vision`, and even then it is not a precise visual check.
- **Pass/fail is judged by the model**, with a check that the quoted evidence really is on the page. A wrong but real quote could still pass. Read the evidence in the report for tests that matter.
- Tested on Chromium only.



## AI use

I designed the core architecture of this autonomous testing agent, including the ReAct loop, the stateful action memory, and the strategy for piercing Shadow DOMs. I utilized Generative AI as a collaborative pair-programmer to scaffold Playwright boilerplate, iterate on the JSON schema constraints, and refine the JavaScript DOM-extraction snippets. However, I drove all critical engineering decisions such as handling cross-origin popups, preventing infinite action loops, and conducting thorough cross-site validation—through my own manual testing and debugging. I own the final implementation and can confidently walk through the technical rationale behind every part of the codebase.

## Project layout

```
agent.py            the whole tool: page reading, model call, actions, verdict check
requirements.txt    Python dependencies
.env.example        template for your API key (copy to .env)
tests.example.txt   example batch file
reports/            screenshots and JSON results from runs
```