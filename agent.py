import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")  
MAX_ELEMENTS = 150     
MAX_TEXT = 3000
HISTORY_LINES = 12      


class AgentAction(BaseModel):
    action: Literal["click", "type", "select", "press", "navigate", "wait", "done"]
    element_id: Optional[int] = None
    value: Optional[str] = None         
    reasoning: str = ""
    status: Optional[Literal["PASS", "FAIL"]] = None   
    evidence: Optional[str] = None                      


@dataclass
class Result:
    test: str
    status: str         
    reason: str
    evidence: str = ""
    steps: list = field(default_factory=list)
    seconds: float = 0.0


MARKER_JS = r"""
(startId) => {
    let next = startId;
    const out = [];
    const SEL = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=tab], ' +
                '[role=menuitem], [role=checkbox], [role=radio], [role=combobox], [role=option], [onclick], ' +
                '[tabindex]:not([tabindex="-1"])';

    const visible = (el) => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };

    const labelOf = (el) => {
        const bits = [];
        if (el.labels && el.labels.length) bits.push(Array.from(el.labels).map(l => l.innerText).join(' '));
        bits.push(el.getAttribute('aria-label') || '', el.getAttribute('placeholder') || '',
                  el.getAttribute('title') || '', el.getAttribute('alt') || '');
        const tag = el.tagName;
        if (tag !== 'SELECT' && tag !== 'INPUT' && tag !== 'TEXTAREA') bits.push(el.innerText || '');
        if (tag === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type)) bits.push(el.value || '');
        return bits.join(' ').replace(/\s+/g, ' ').trim().substring(0, 80);
    };

    const walk = (root) => {
        root.querySelectorAll('[data-ai-id]').forEach(e => e.removeAttribute('data-ai-id'));
        root.querySelectorAll(SEL).forEach(el => {
            if (!visible(el)) return;
            const id = next++;
            el.setAttribute('data-ai-id', id);
            const item = {
                id: id,
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                role: el.getAttribute('role') || '',
                label: labelOf(el),
                disabled: !!el.disabled,
            };
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                item.value = el.type === 'password' ? (el.value ? '***' : '') : (el.value || '').substring(0, 40);
                if (el.type === 'checkbox' || el.type === 'radio') item.checked = el.checked;
            }
            if (el.tagName === 'SELECT') {
                item.options = Array.from(el.options).slice(0, 15).map(o => o.text.trim());
                item.selected = el.selectedIndex >= 0 ? el.options[el.selectedIndex].text.trim() : '';
            }
            if (el.tagName === 'A') item.href = (el.getAttribute('href') || '').substring(0, 60);
            out.push(item);
        });
        root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) walk(el.shadowRoot); });
    };

    walk(document);
    return out;
}
"""

PAGE_TEXT_JS = "() => (document.body ? document.body.innerText : '')"


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def describe(e: dict) -> str:
    bits = [f"[{e['id']}] {e['tag']}"]
    if e.get("type"):
        bits.append(f"type={e['type']}")
    if e.get("role"):
        bits.append(f"role={e['role']}")
    bits.append(f'label="{e.get("label", "")}"')
    if "value" in e:
        bits.append(f'value="{e["value"]}"')
    if "checked" in e:
        bits.append(f"checked={e['checked']}")
    if e.get("options"):
        bits.append(f"options={e['options']} selected=\"{e.get('selected', '')}\"")
    if e.get("href"):
        bits.append(f"href={e['href']}")
    if e.get("disabled"):
        bits.append("DISABLED")
    return " ".join(bits)


@dataclass
class Observation:
    url: str
    title: str
    full_text: str                     
    elements: list
    handles: dict                       


class Session:
    """One browser context. Follows pop-ups and new tabs, and accepts alert dialogs."""

    def __init__(self, context):
        self.context = context
        self.page = None
        context.on("page", self._on_new_page)

    def _on_new_page(self, page):
        self._attach(page)
        self.page = page            
    def _attach(self, page):
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

    async def open(self, url: str):
        page = await self.context.new_page()
        self.page = page
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await settle(page)

    def current(self):
        if self.page is None or self.page.is_closed():
            alive = [p for p in self.context.pages if not p.is_closed()]
            if not alive:
                raise RuntimeError("The browser page was closed.")
            self.page = alive[-1]
        return self.page


async def settle(page):
    """Wait for the page to stop loading, without hanging forever on sites that never go idle."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightTimeout:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except PlaywrightTimeout:
        pass
    await page.wait_for_timeout(400)


async def observe(session: Session) -> Observation:
    last_error = None
    for _ in range(3):
        page = session.current()
        try:
            elements, handles, texts = [], {}, []
            next_id = 1
            for frame in page.frames:
                try:
                    items = await frame.evaluate(MARKER_JS, next_id)
                    text = await frame.evaluate(PAGE_TEXT_JS)
                except Exception:
                    continue                                   
                for item in items:
                    handles[item["id"]] = frame
                elements.extend(items)
                next_id += len(items)
                texts.append(text)
            return Observation(page.url, await page.title(), squash(" ".join(texts)), elements, handles)
        except Exception as e: 
            last_error = e                                 
            await settle(page)
            
    raise RuntimeError(f"Could not read the page: {last_error}")




class ActionError(Exception):
    pass


def with_scheme(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ActionError("navigate needs a URL in 'value'.")
    return url if re.match(r"^[a-z]+://", url, re.I) else "https://" + url


async def act(session: Session, obs: Observation, a: AgentAction) -> str:
    page = session.current()

    if a.action == "navigate":
        await page.goto(with_scheme(a.value), wait_until="domcontentloaded", timeout=30000)
        return f"opened {page.url}"

    if a.action == "wait":
        try:
            seconds = min(max(float(a.value or 2), 0.5), 10)
        except ValueError:
            seconds = 2
        await page.wait_for_timeout(int(seconds * 1000))
        return f"waited {seconds:g}s"

    if a.action == "press" and a.element_id is None:
        await page.keyboard.press(a.value or "Enter")
        return f"pressed {a.value or 'Enter'}"

    if a.element_id is None or a.element_id not in obs.handles:
        raise ActionError(f"There is no element with id {a.element_id}. Use an id from the list.")
    target = obs.handles[a.element_id].locator(f"[data-ai-id='{a.element_id}']").first

    if a.action == "click":
        try:
            await target.click(timeout=5000)
        except Exception:
            try:
                await target.click(timeout=3000, force=True)
            except Exception:
                await target.evaluate("el => el.click()")
        return "clicked"

    if a.action == "type":
        if a.value is None:
            raise ActionError("type needs the text in 'value'.")
        try:
            await target.fill(a.value, timeout=5000)
        except Exception:
            await target.click(timeout=3000)
            await page.keyboard.type(a.value)
        return f"typed {'***' if _is_secret(obs, a.element_id) else repr(a.value)}"

    if a.action == "select":
        if a.value is None:
            raise ActionError("select needs the option text in 'value'.")
        try:
            await target.select_option(label=a.value, timeout=5000)
        except Exception:
            await target.select_option(value=a.value, timeout=3000)
        return f"selected {a.value!r}"

    if a.action == "press":
        await target.press(a.value or "Enter", timeout=5000)
        return f"pressed {a.value or 'Enter'}"

    raise ActionError(f"Unknown action {a.action!r}.")


def _is_secret(obs: Observation, element_id: int) -> bool:
    return any(e["id"] == element_id and e.get("type") == "password" for e in obs.elements)




PROMPT = """You are an autonomous QA test agent controlling a real web browser.

TEST CASE (written by a human): {test}
{data_block}
Work out what the test case expects, then perform the steps in the browser one action at a time.

CURRENT PAGE
URL: {url}
Title: {title}
Visible text (may be shortened): {text}

INTERACTIVE ELEMENTS (refer to them by the number in brackets):
{elements}

WHAT YOU DID SO FAR:
{history}

RULES
- Reply with ONE action as a JSON object, and nothing else:
  {{"action": "click|type|select|press|navigate|wait|done", "element_id": <number or null>, "value": <string or null>,
   "reasoning": "<one short sentence>", "status": "PASS|FAIL|null", "evidence": "<text or null>"}}
- click: needs element_id.  type: element_id and value (the text).  select: element_id of a dropdown and value (the option text).
- press: value is a key such as Enter; element_id optional.  navigate: value is a URL.  wait: value is seconds.
- Only use element ids that appear in the list above. If something you need is not there, scroll is not available: try another route.
- Use the TEST DATA exactly as given. Do not invent credentials. If the page itself displays demo credentials, you may use those.
- When you can tell whether the test case holds, reply with action "done".
  PASS means everything the test case expects is true on the page right now. If the test case expects an error message
  (for example a wrong password), then seeing that error is a PASS.
  FAIL means the expectation is not met, or the task cannot be completed (say why in "reasoning").
- For a PASS you MUST give "evidence": a short piece of text copied exactly from the visible text, URL or title above
  that proves it. A PASS without real evidence is rejected.
- Do not repeat an action that did not help."""


def build_prompt(test: str, data: dict, obs: Observation, history: list) -> str:
    data_block = ""
    if data:
        data_block = "TEST DATA (use exactly): " + json.dumps(data) + "\n"
    shown = obs.elements[:MAX_ELEMENTS]
    elements = "\n".join(describe(e) for e in shown) or "(no interactive elements found)"
    if len(obs.elements) > MAX_ELEMENTS:
        elements += f"\n... and {len(obs.elements) - MAX_ELEMENTS} more not shown"
    text = obs.full_text[:MAX_TEXT] + (" ..." if len(obs.full_text) > MAX_TEXT else "")
    return PROMPT.format(
        test=test, data_block=data_block, url=obs.url, title=obs.title, text=text or "(empty)",
        elements=elements, history="\n".join(history[-HISTORY_LINES:]) or "(nothing yet)",
    )


def parse_action(raw: str) -> AgentAction:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    return AgentAction.model_validate_json(raw)


class GeminiPlanner:
    def __init__(self, model: str):
        from google import genai
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            raise SystemExit("Set GEMINI_API_KEY (in your environment or a .env file) first.")
        self.client = genai.Client()
        self.model = model

    async def next_action(self, prompt: str, screenshot: Optional[bytes] = None) -> AgentAction:
        from google.genai import types
        contents = [prompt]
        if screenshot:
            contents = [types.Part.from_bytes(data=screenshot, mime_type="image/png"), prompt]
        last_error = None
        for attempt in range(3):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
                )
                return parse_action(response.text)
            except Exception as e:
                last_error = e
                if "not found" in str(e).lower() or "404" in str(e):
                    raise RuntimeError(f"The model '{self.model}' was not found. Set GEMINI_MODEL to a valid model name.") from e
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f"The model call failed 3 times: {last_error}")




def evidence_is_real(evidence: Optional[str], obs: Observation) -> bool:
    e = squash(evidence).lower()
    if len(e) < 3:
        return False
    return e in obs.full_text.lower() or e in obs.url.lower() or e in obs.title.lower()


async def run_test(planner, test: str, url: str, data: dict, max_steps: int = 15,
                   headless: bool = False, vision: bool = False, report_dir: Optional[Path] = None) -> Result:
    started = time.time()
    steps, history = [], []
    rejected = 0
    repeats, last_key = 0, None
    result = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        session = Session(context)
        try:
            await session.open(url)

            for step in range(1, max_steps + 1):
                obs = await observe(session)
                shot = await session.current().screenshot() if vision else None
                action = await planner.next_action(build_prompt(test, data, obs, history), shot)
                print(f"  step {step}: {action.action} id={action.element_id} value={action.value!r} - {action.reasoning}")

                if action.action == "done":
                    if action.status == "PASS":
                        if evidence_is_real(action.evidence, obs):
                            result = Result(test, "PASS", action.reasoning, squash(action.evidence))
                            break
                        rejected += 1
                        history.append(f"{step}. you said PASS, REJECTED: the evidence {action.evidence!r} is not on the page.")
                        if rejected >= 2:
                            result = Result(test, "FAIL", "The model could not back up a PASS with evidence from the page.")
                            break
                        continue
                    result = Result(test, "FAIL", action.reasoning or "The model reported FAIL.")
                    break

                key = (action.action, action.element_id, action.value)
                repeats = repeats + 1 if key == last_key else 1
                last_key = key
                if repeats >= 4:
                    result = Result(test, "ERROR", "The agent got stuck repeating the same action.")
                    break

                label = next((e["label"] for e in obs.elements if e["id"] == action.element_id), "")
                try:
                    outcome = await act(session, obs, action)
                    await settle(session.current())
                except ActionError as e:
                    outcome = f"FAILED: {e}"
                except Exception as e:
                    outcome = f"FAILED: {str(e).splitlines()[0][:150]}"
                line = f"{step}. {action.action} [{action.element_id}] {label!r} {action.value or ''} -> {outcome}"
                history.append(line)
                steps.append(line)
                if repeats == 3:
                    history.append("   (you have repeated this three times; try something different)")

            if result is None:
                result = Result(test, "ERROR", f"No verdict after {max_steps} steps.")
        except Exception as e:
            result = Result(test, "ERROR", str(e).splitlines()[0][:200] if str(e) else type(e).__name__)
        finally:
            result.steps = steps
            result.seconds = round(time.time() - started, 1)
            if report_dir:
                try:
                    report_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    slug = re.sub(r"[^a-z0-9]+", "-", test.lower())[:40].strip("-")
                    await session.current().screenshot(path=str(report_dir / f"{stamp}-{slug}.png"))
                    (report_dir / f"{stamp}-{slug}.json").write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
                except Exception:
                    pass
            await browser.close()
    return result




def parse_data(items) -> dict:
    data = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--data expects key=value, got {item!r}")
        key, value = item.split("=", 1)
        data[key.strip()] = value
    return data


def load_tests(args):
    """Each line of --tests-file is either 'test text' or 'url || test text'. Lines starting with # are skipped."""
    if args.tests_file:
        tests = []
        for line in Path(args.tests_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "||" in line:
                url, text = [s.strip() for s in line.split("||", 1)]
            else:
                url, text = args.url, line
            if not url:
                raise SystemExit("A test has no URL. Pass --url or write 'url || test' on the line.")
            tests.append((url, text))
        return tests
    if not args.test or not args.url:
        raise SystemExit("Give --url and --test (or use --tests-file).")
    return [(args.url, args.test)]


async def main_async(args) -> int:
    planner = GeminiPlanner(args.model)
    data = parse_data(args.data)
    results = []
    for url, test in load_tests(args):
        print(f"\nTEST: {test}\nURL:  {url}")
        r = await run_test(planner, test, url, data, args.max_steps, args.headless, args.vision, Path(args.report_dir))
        print(f"RESULT: {r.status} - {r.reason}" + (f"  (evidence: \"{r.evidence}\")" if r.evidence else ""))
        results.append(r)

    if len(results) > 1:
        print("\nSUMMARY")
        for r in results:
            print(f"  {r.status:5} {r.seconds:>5}s  {r.test}")
    if any(r.status == "ERROR" for r in results):
        return 2
    return 1 if any(r.status == "FAIL" for r in results) else 0


def main():
    parser = argparse.ArgumentParser(description="Run a plain-English test case in a real browser.")
    parser.add_argument("--url", help="page to start on")
    parser.add_argument("--test", help="the test case in plain English")
    parser.add_argument("--tests-file", help="file with one test per line ('test' or 'url || test')")
    parser.add_argument("--data", nargs="*", metavar="KEY=VALUE", help="test data such as email=a@b.com password=secret")
    parser.add_argument("--headless", action="store_true", help="hide the browser window")
    parser.add_argument("--vision", action="store_true", help="also send a screenshot to the model each step")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
