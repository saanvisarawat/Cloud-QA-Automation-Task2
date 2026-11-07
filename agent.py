import os
import json
import asyncio
from playwright.async_api import async_playwright
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import argparse
load_dotenv()

# Ensure API key is present
if not os.environ.get("GEMINI_API_KEY"):
    raise ValueError("GEMINI_API_KEY environment variable not set.")

client = genai.Client()

# Define the structured output we want from Gemini
class AgentAction(BaseModel):
    action: str = Field(description="The action to take: 'click', 'type', 'navigate', or 'done'.")
    element_id: int | None = Field(description="The numeric ID of the element to interact with, if applicable.", default=None)
    value: str | None = Field(description="The text to type or URL to navigate to, if applicable.", default=None)
    reasoning: str = Field(description="Brief explanation of why this action was chosen.")
    status: str | None = Field(description="If action is 'done', report 'PASS' or 'FAIL'.", default=None)

# JavaScript snippet to tag interactive elements with unique IDs
MARKER_JS = """
() => {
    let idCounter = 1;
    const elements = [];
    const interactables = document.querySelectorAll('a, button, input, select, textarea, [role="button"], [tabindex="0"]');
    
    interactables.forEach(el => {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden') {
            const id = idCounter++;
            el.setAttribute('data-ai-id', id);
            
            let name = el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.name || '';
            name = name.trim().replace(/\\s+/g, ' ').substring(0, 50);
            
            elements.push({
                id: id,
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                name: name
            });
        }
    });
    return JSON.stringify(elements);
}
"""

async def run_test(instruction: str, start_url: str):
    print(f"Goal: {instruction}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False) # Keep visible for the recording
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto(start_url)
        
        # Loop limit to prevent infinite AI scrolling/clicking
        max_steps = 10 
        
        for step in range(max_steps):
            print(f"\n--- Step {step + 1} ---")
            await page.wait_for_load_state("networkidle")
            
            # 1. Map the DOM
            dom_json = await page.evaluate(MARKER_JS)
            
            # 2. Prompt Gemini
            prompt = f"""
            You are an autonomous QA testing agent. 
            Your goal is: "{instruction}"
            
            Current URL: {page.url}
            
            Here are the visible interactive elements on the page:
            {dom_json}
            
            Based on the goal and the current page elements, what is the exact next step?
            If the goal has been successfully verified on the screen, output action 'done' and status 'PASS' or 'FAIL'.
            """
            
            response = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AgentAction,
                ),
            )
            
            # 3. Parse and Execute the Action
            action_data = json.loads(response.text)
            print(f"AI Decision: {action_data}")
            
            action = action_data.get("action")
            
            if action == "done":
                print(f"\n[TEST COMPLETE] Status: {action_data.get('status')} - Reason: {action_data.get('reasoning')}")
                break
                
            elif action == "click":
                target = page.locator(f"[data-ai-id='{action_data['element_id']}']")
                await target.click()
                
            elif action == "type":
                target = page.locator(f"[data-ai-id='{action_data['element_id']}']")
                await target.fill(action_data['value'])
                
            elif action == "navigate":
                await page.goto(action_data['value'])
                
            else:
                print("Unknown action received.")
                break
                
            await asyncio.sleep(2) # Give page a moment to react before next step
            
        await browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous AI QA Agent")
    parser.add_argument("--url", type=str, default="https://phptravels.com/demo", help="The starting URL")
    parser.add_argument("--test", type=str, default="Verify that the login button is visible on the page.", help="The plain-English test case")
    args = parser.parse_args()
    
    asyncio.run(run_test(args.test, args.url))