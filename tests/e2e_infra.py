"""Real-browser smoke/E2E for the Infra Console.

CI starts the actual FastAPI backend plus Vite dev server. The browser signs in through
the UI, mutates state through same-origin HTTP, observes SSE-driven refresh, exercises
replay comparison and verifies the managed backup/restore-drill controls.
"""
from __future__ import annotations

import re
from playwright.sync_api import sync_playwright, expect


BASE="http://127.0.0.1:5173"
PASSWORD="test-password-very-long"


def main() -> None:
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1000})
        page.goto(BASE,wait_until="domcontentloaded")
        page.get_by_label("账户密码").fill(PASSWORD)
        page.get_by_role("button",name="登录").click()
        expect(page.get_by_text("Backend · HTTP")).to_be_visible(timeout=10_000)

        # Create a project through the same authenticated browser session. AppProvider
        # must discover it through the real SSE invalidation stream without a reload.
        project=page.evaluate("""async () => {
          const r=await fetch('/api/v1/projects',{
            method:'POST',credentials:'same-origin',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({name:'E2E Project',description:'browser infra e2e'})
          });
          if(!r.ok)throw new Error('project create '+r.status+' '+await r.text());
          return await r.json();
        }""")
        expect(page.locator(".project-select")).to_contain_text("E2E Project",timeout=10_000)

        page.get_by_role("button",name=re.compile("Switch to Infra Core")).click()
        expect(page.get_by_role("heading",name="Infra Overview")).to_be_visible()
        infra_nav=["".join(text.split()) for text in page.locator(".sidebar .nav-item").all_inner_texts()]
        assert infra_nav==[
            "⌂Overview","≡Events&Replay","⇄Scheduler&Leases","◇Memory&Context",
            "⛓Provenance","↗Invocations","⌁Consistency&Recovery",
        ],infra_nav
        assert "Tasks" not in "\n".join(infra_nav)

        page.get_by_role("button",name="Events & Replay").click()
        expect(page.get_by_text("PROJECT_CREATED").first).to_be_visible(timeout=10_000)

        # While staying on Events & Replay, create a task. TASK_CREATED must appear
        # through SSE-triggered re-fetch rather than a browser reload.
        task=page.evaluate("""async (projectId) => {
          const r=await fetch('/api/v1/projects/'+encodeURIComponent(projectId)+'/tasks',{
            method:'POST',credentials:'same-origin',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
              projectId,title:'E2E replay task',description:'exercise replay diff',
              dependencyIds:[],acceptanceCriteria:[]
            })
          });
          if(!r.ok)throw new Error('task create '+r.status+' '+await r.text());
          return await r.json();
        }""",project["id"])
        expect(page.get_by_text("TASK_CREATED").first).to_be_visible(timeout=10_000)

        # Point-in-time replay + deterministic diff.
        page.locator("tr",has_text="PROJECT_CREATED").first.click()
        page.get_by_role("button",name="Set as compare A").click()
        page.locator("tr",has_text="TASK_CREATED").first.click()
        compare_button=page.get_by_role("button",name=re.compile(r"Compare #\d+ → #\d+"))
        expect(compare_button).to_be_enabled()
        compare_button.click()
        expect(page.locator(".infra-diff-section").filter(has_text="tasks")).to_be_visible(timeout=10_000)
        expect(page.locator(".infra-diff-section").filter(has_text=task["id"])).to_be_visible()

        # A2CM is a real evidence-backed candidate workflow, not a static read model.
        page.get_by_role("button",name="Memory & Context").click()
        page.get_by_role("button",name="A2CM beliefs").click()
        page.get_by_role("button",name="Enable A2CM Primary reviewer").click()
        expect(page.get_by_text(re.compile("A2CM Primary session"))).to_be_visible(timeout=10_000)
        page.get_by_label("Observation").fill("The project-created evidence supports the E2E hypothesis.")
        page.get_by_role("button",name="Record candidate").click()
        expect(page.get_by_text(re.compile("Created evidence-backed candidate"))).to_be_visible(timeout=10_000)
        page.get_by_label("Decision").select_option("PROMOTE")
        page.get_by_label("Rationale").fill("E2E promotion after evidence-backed Primary review.")
        page.get_by_role("button",name="Commit Primary review").click()
        expect(page.get_by_text(re.compile("→ PROMOTED"))).to_be_visible(timeout=10_000)

        # Frontier surfaces are factual capability gates. CI has no CCR/worker binary,
        # so neither paid CONSULT nor child DELEGATE may pretend to be available.
        page.locator(".sidebar .nav-item").filter(has_text="Provenance").click()
        expect(page.get_by_text(re.compile("CCR_API_KEY is not configured"))).to_be_visible(timeout=10_000)
        expect(page.get_by_text("MRLC_FRONTIER_ALLOW_DELEGATION is disabled.")).to_be_visible()
        expect(page.get_by_role("button",name="RUN DELEGATE")).to_have_count(0)

        # Managed backup and an isolated restore drill are real server operations.
        page.get_by_role("button",name="Consistency & Recovery").click()
        page.get_by_role("button",name="Create backup").click()
        expect(page.get_by_text(re.compile("created and checksummed"))).to_be_visible(timeout=15_000)
        verify=page.get_by_role("button",name="Verify latest by restore drill")
        expect(verify).to_be_enabled()
        verify.click()
        expect(page.get_by_text(re.compile("Restore drill .* passed checksum verification"))).to_be_visible(timeout=15_000)

        page.get_by_role("button",name=re.compile("Back to Control Room")).click()
        expect(page.locator(".sidebar .nav-item").filter(has_text="Tasks")).to_be_visible()
        expect(page.locator(".sidebar .nav-item").filter(has_text="Events & Replay")).to_have_count(0)

        browser.close()


if __name__=="__main__":
    main()
