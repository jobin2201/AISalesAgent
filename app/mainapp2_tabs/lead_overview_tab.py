import streamlit as st
import streamlit.components.v1 as components
import html
import os
import subprocess
import sys
import tempfile
import textwrap
from datetime import date


def _draft_linkedin_message_same_window(target_profile_url: str, draft_message: str) -> tuple[bool, str]:
    """Prepare a LinkedIn message draft by auto-clicking Message and filling modal fields."""
    safe_msg = (draft_message or "").strip()
    if len(safe_msg) < 25:
        safe_msg = (safe_msg + " We would like to connect and share a concise relevant update.").strip()
    safe_msg = safe_msg[:750]
    script = textwrap.dedent(
        """
        import os
        from playwright.sync_api import sync_playwright, Error as PlaywrightError

        target_url = os.environ.get("LI_TARGET_URL", "").strip()
        msg = os.environ.get("LI_DRAFT_MSG", "").strip()
        if not target_url:
            raise RuntimeError("Missing LinkedIn target URL")

        def debug(msg):
            print(f"[LI-DEBUG] {msg}")

        def _try_fill_compose(page, message_text):
            topic = page.locator("select#msg-shared-modals-msg-page-modal-presenter-conversation-topic").first
            if topic.count() > 0:
                debug("Topic dropdown detected. Selecting first valid topic.")
                opts = topic.locator("option[value]")
                for i in range(opts.count()):
                    val = opts.nth(i).get_attribute("value") or ""
                    if val.strip():
                        topic.select_option(val)
                        page.wait_for_timeout(400)
                        break

            ta = page.locator("textarea#org-message-page-modal-message").first
            if ta.count() > 0:
                debug("Filling exact modal textarea #org-message-page-modal-message.")
                ta.click()
                ta.fill(message_text)
                return True

            ta2 = page.locator("textarea[placeholder*='Write a message'], textarea[name='message']").first
            if ta2.count() > 0:
                debug("Filling fallback textarea selector.")
                ta2.click()
                ta2.fill(message_text)
                return True

            ce = page.locator("div.msg-form__contenteditable, div[contenteditable='true']").first
            if ce.count() > 0:
                debug("Filling contenteditable composer fallback.")
                ce.click()
                ce.fill(message_text)
                return True

            return False

        def fill_modal(page, message_text):
            # If user already clicked Message manually and modal is open, fill immediately.
            existing_modal = page.locator("textarea#org-message-page-modal-message").first
            if existing_modal.count() > 0:
                debug("Detected already-open message modal (manual click path).")
                existing_modal.click()
                existing_modal.fill(message_text)
                return True

            selectors = [
                "button[data-test-message-page-button]",
                "button.org-top-card-primary-actions__action[data-test-message-page-button]",
                "button[aria-label^='Message']",
                "button:has-text('Message')",
                "a:has-text('Message')",
            ]

            for sel in selectors:
                try:
                    cnt = page.locator(sel).count()
                except Exception:
                    cnt = -1
                debug(f"Selector count {sel} -> {cnt}")

            action = None
            for _ in range(12):
                for sel in selectors:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        action = loc
                        break
                if action is not None:
                    break
                page.wait_for_timeout(500)

            if action is None:
                more = page.locator("button:has-text('More'), button[aria-label*='More']").first
                if more.count() > 0:
                    debug("Primary message button not found. Trying More overflow.")
                    more.click()
                    page.wait_for_timeout(800)
                    action = page.locator("span:has-text('Message'), span:has-text('Send in a message')").first
                else:
                    debug("More overflow button not found.")

            if action is None or action.count() == 0:
                debug("Message action not found after retries.")
                return False

            try:
                action.scroll_into_view_if_needed(timeout=4000)
            except Exception:
                pass

            pages_before = len(page.context.pages)

            try:
                debug("Clicking Message action (normal click).")
                action.click(timeout=5000)
            except Exception:
                debug("Normal click failed; trying force click.")
                action.click(force=True, timeout=5000)

            # Wait/poll for compose UI to appear on current page.
            for _ in range(12):
                if _try_fill_compose(page, message_text):
                    return True
                page.wait_for_timeout(500)

            # Some LinkedIn variants open compose in another page/tab.
            pages_after = page.context.pages
            if len(pages_after) > pages_before:
                debug("Detected additional page after Message click. Checking new page for composer.")
                new_page = pages_after[-1]
                try:
                    new_page.bring_to_front()
                except Exception:
                    pass
                for _ in range(12):
                    if _try_fill_compose(new_page, message_text):
                        return True
                    new_page.wait_for_timeout(500)

            # Retry one more click path if UI was slow.
            try:
                debug("Retrying Message click with force after no compose detected.")
                action.click(force=True, timeout=5000)
                for _ in range(10):
                    if _try_fill_compose(page, message_text):
                        return True
                    page.wait_for_timeout(500)
            except Exception:
                pass

            debug("No message compose area found after click.")
            return False

        with sync_playwright() as p:
            browser = None
            launch_errors = []
            for mode in ({"channel": "msedge", "headless": False}, {"channel": "chrome", "headless": False}, {"headless": False}):
                try:
                    browser = p.chromium.launch(**mode)
                    break
                except Exception as exc:
                    launch_errors.append(str(exc))

            if browser is None:
                raise RuntimeError("Unable to launch browser: " + " | ".join(launch_errors[:2]))

            context = browser.new_context()
            page = context.new_page()
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
            debug("Opened LinkedIn login page in automated browser.")

            # Wait for manual login if needed.
            logged_in = False
            for _ in range(240):
                u = (page.url or "").lower()
                if "linkedin.com/login" not in u and "checkpoint" not in u:
                    logged_in = True
                    break
                page.wait_for_timeout(1000)

            if not logged_in:
                raise RuntimeError("Login not completed in time in automated browser")

            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            debug(f"Navigated to target URL: {target_url}")
            if not fill_modal(page, msg):
                raise RuntimeError("Could not click Message and fill draft modal")

            print("AUTOMATION_OK: Draft prepared. Please click Send manually.")
            try:
                page.wait_for_timeout(120000)
            except PlaywrightError:
                # User may close page/browser after draft is prepared; treat as normal.
                print("AUTOMATION_INFO: Page/browser closed after draft prep.")
        """
    )

    with tempfile.NamedTemporaryFile("w", suffix="_linkedin_draft.py", delete=False, encoding="utf-8") as tf:
        tf.write(script)
        script_path = tf.name

    env = os.environ.copy()
    env["LI_TARGET_URL"] = target_profile_url
    env["LI_DRAFT_MSG"] = safe_msg

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=420,
        )
    except Exception as exc:
        return False, f"Automation launch failed: {exc}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()

    if out:
        print("[LI-DEBUG-STDOUT]", out)
    if err:
        print("[LI-DEBUG-STDERR]", err)

    if proc.returncode == 0:
        if "AUTOMATION_INFO: Page/browser closed after draft prep." in out:
            return True, "Message wasn't sent. Click LinkedIn Connect again to send the message."
        if "AUTOMATION_OK:" in out:
            return True, "Draft prepared. Please click Send manually."
        return True, "LinkedIn flow completed."

    # If draft was already prepared, do not mark as failure even if a later wait/read call errored.
    if "AUTOMATION_OK:" in out:
        return True, "Draft prepared. Please click Send manually."

    # If user closes the browser/page quickly, treat as user-cancelled instead of error.
    closed_markers = [
        "TargetClosedError",
        "Target page, context or browser has been closed",
        "Page.wait_for_timeout: Target page, context or browser has been closed",
    ]
    combined = (out + "\n" + err).strip()
    if any(marker in combined for marker in closed_markers):
        return True, "LinkedIn window was closed by user before completion. No message was sent."

    if "No module named 'playwright'" in err:
        return False, "Playwright missing. Install with: pip install playwright && playwright install chromium"

    return False, err or out or "LinkedIn automation failed"


def render_lead_overview_tab(selected_lead: dict, format_list) -> None:
    raw_company_name = str(selected_lead.get("company_name", "Lead") or "Lead")
    company_name = html.escape(selected_lead.get("company_name", "Lead") or "Lead")
    priority = html.escape(str(selected_lead.get("priority", "—") or "—"))
    score = html.escape(str(selected_lead.get("total_score", 0)))
    recommended_action = html.escape(str(selected_lead.get("recommended_action", "—") or "—"))

    st.markdown(
        """
        <style>
        .lead-overview-hero {
            background: linear-gradient(180deg, #eaf3ff 0%, #ffffff 100%);
            border: 1px solid #bfd6f3;
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 10px;
        }
        .lead-overview-kicker {
            font-size: 0.74rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #245892;
            font-weight: 800;
            margin-bottom: 2px;
        }
        .lead-overview-title {
            font-size: 1.12rem;
            color: #10213a;
            font-weight: 800;
            margin-bottom: 2px;
        }
        .lead-overview-subtitle {
            font-size: 0.9rem;
            color: #365273;
            font-weight: 600;
            margin-bottom: 0;
        }
        .lead-overview-chip-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 10px;
            margin: 10px 0 12px 0;
        }
        .lead-overview-chip {
            border-radius: 12px;
            padding: 10px 12px;
            border: 1px solid #c2d8f5;
            background: #f6faff;
        }
        .lead-overview-chip:nth-child(2) {
            background: #edf7ff;
            border-color: #b6d6f5;
        }
        .lead-overview-chip:nth-child(3) {
            background: #f2f7ff;
            border-color: #c8daf2;
        }
        .lead-overview-chip-label {
            font-size: 0.72rem;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: #3e5f84;
            font-weight: 800;
            margin-bottom: 4px;
        }
        .lead-overview-chip-value {
            font-size: 0.98rem;
            color: #10213a;
            font-weight: 750;
            line-height: 1.35;
            word-break: break-word;
        }
        .lead-overview-section-title {
            font-size: 1rem;
            color: #113662;
            font-weight: 800;
            margin-bottom: 4px;
        }
        .lead-overview-item-label {
            color: #335372;
            font-weight: 700;
            margin-bottom: 2px;
        }
        .lead-overview-item-value {
            color: #10213a;
            font-weight: 600;
            margin-bottom: 10px;
            word-break: break-word;
        }
        @keyframes leadOverviewInBoxFade {
            from {
                opacity: 0;
                transform: translateY(6px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker) .lead-overview-item-label,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-right-marker) .lead-overview-item-label,
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker) .lead-overview-item-label,
        [data-testid="stVerticalBlock"]:has(.lead-overview-right-marker) .lead-overview-item-label,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker) .lead-overview-item-value,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-right-marker) .lead-overview-item-value,
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker) .lead-overview-item-value,
        [data-testid="stVerticalBlock"]:has(.lead-overview-right-marker) .lead-overview-item-value {
            animation: leadOverviewInBoxFade 0.34s ease both;
            transition: color 0.2s ease, transform 0.2s ease;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker) .lead-overview-item-value:hover,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-right-marker) .lead-overview-item-value:hover,
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker) .lead-overview-item-value:hover,
        [data-testid="stVerticalBlock"]:has(.lead-overview-right-marker) .lead-overview-item-value:hover {
            transform: translateX(2px);
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker),
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-right-marker),
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker),
        [data-testid="stVerticalBlock"]:has(.lead-overview-right-marker) {
            transition: border-color 0.24s ease, transform 0.24s ease;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker):hover,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-right-marker):hover,
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker):hover,
        [data-testid="stVerticalBlock"]:has(.lead-overview-right-marker):hover {
            border-color: rgba(108, 150, 204, 0.65);
            transform: translateY(-1px);
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker) .stLinkButton > a,
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker) .stLinkButton > a,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker) .stButton > button,
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker) .stButton > button {
            transition: transform 0.2s ease, filter 0.2s ease;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker) .stLinkButton > a:hover,
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker) .stLinkButton > a:hover,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker) .stButton > button:hover,
        [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker) .stButton > button:hover {
            transform: translateY(-1px);
            filter: saturate(1.05);
        }
        @media (prefers-color-scheme: dark) {
            .lead-overview-hero {
                background: linear-gradient(180deg, rgba(30, 58, 95, 0.92) 0%, rgba(18, 28, 45, 0.95) 100%);
                border-color: rgba(117, 167, 233, 0.55);
            }
            .lead-overview-kicker {
                color: #9bc8ff;
            }
            .lead-overview-title {
                color: #f4f8ff;
            }
            .lead-overview-subtitle {
                color: #d0e2ff;
            }
            .lead-overview-chip {
                background: rgba(24, 39, 60, 0.9);
                border-color: rgba(117, 167, 233, 0.45);
            }
            .lead-overview-chip:nth-child(2),
            .lead-overview-chip:nth-child(3) {
                background: rgba(23, 38, 58, 0.9);
                border-color: rgba(117, 167, 233, 0.42);
            }
            .lead-overview-chip-label {
                color: #9cc8ff;
            }
            .lead-overview-chip-value {
                color: #f3f7ff;
            }
            .lead-overview-section-title {
                color: #a6cdff;
            }
            .lead-overview-item-label {
                color: #97c4ff;
            }
            .lead-overview-item-value {
                color: #edf3ff;
            }
            [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-left-marker):hover,
            [data-testid="stVerticalBlockBorderWrapper"]:has(.lead-overview-right-marker):hover,
            [data-testid="stVerticalBlock"]:has(.lead-overview-left-marker):hover,
            [data-testid="stVerticalBlock"]:has(.lead-overview-right-marker):hover {
                border-color: rgba(117, 167, 233, 0.7);
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="lead-overview-hero">
            <div class="lead-overview-kicker">Lead Overview</div>
            <div class="lead-overview-title">{company_name}</div>
            <div class="lead-overview-subtitle">Organized company context and parsed lead intelligence in one place.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="lead-overview-chip-grid">
            <div class="lead-overview-chip">
                <div class="lead-overview-chip-label">Priority</div>
                <div class="lead-overview-chip-value">{priority}</div>
            </div>
            <div class="lead-overview-chip">
                <div class="lead-overview-chip-label">Score</div>
                <div class="lead-overview-chip-value">{score}</div>
            </div>
            <div class="lead-overview-chip">
                <div class="lead-overview-chip-label">Recommended Action</div>
                <div class="lead-overview-chip-value">{recommended_action}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.1, 0.9])

    with left:
        with st.container(border=True):
            st.markdown('<div class="lead-overview-left-marker" style="display:none;"></div>', unsafe_allow_html=True)
            st.markdown('<div class="lead-overview-section-title">Company Snapshot</div>', unsafe_allow_html=True)
            description = selected_lead.get("description", "") or "—"
            st.markdown('<div class="lead-overview-item-label">Description</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="lead-overview-item-value">{html.escape(str(description))}</div>', unsafe_allow_html=True)

            st.markdown('<div class="lead-overview-item-label">Product</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="lead-overview-item-value">{html.escape(str(selected_lead.get("product_description", "") or "—"))}</div>',
                unsafe_allow_html=True,
            )

            st.markdown('<div class="lead-overview-item-label">Industry</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="lead-overview-item-value">{html.escape(str(selected_lead.get("industry", "") or "—"))}</div>',
                unsafe_allow_html=True,
            )

            if selected_lead.get("website"):
                st.link_button("Open Website", selected_lead["website"], use_container_width=True)
            for idx, url in enumerate(selected_lead.get("linkedin_urls", []) or []):
                st.link_button(f"LinkedIn {idx + 1}", url, use_container_width=True)

            st.divider()
            st.markdown('<div class="lead-overview-section-title">LinkedIn Outreach</div>', unsafe_allow_html=True)

            key_base = raw_company_name.strip().lower().replace(" ", "_") or "lead"
            lead_linkedin_default = ""
            lead_urls = selected_lead.get("linkedin_urls", []) or []
            if lead_urls:
                lead_linkedin_default = str(lead_urls[0] or "").strip()
            daily_limit = int(os.getenv("LINKEDIN_MAX_CONNECTIONS_PER_DAY", "20") or "20")
            day_key = f"lead_overview_linkedin_day_{key_base}"
            count_key = f"lead_overview_linkedin_count_{key_base}"
            today = date.today().isoformat()
            if st.session_state.get(day_key) != today:
                st.session_state[day_key] = today
                st.session_state[count_key] = 0

            requests_today = int(st.session_state.get(count_key, 0))
            st.caption(
                f"One-click open in next tab (LinkedIn style) with auto-draft when CDP attach is available. "
                f"Daily limit: {requests_today}/{daily_limit}."
            )

            status_key = f"lead_overview_linkedin_status_{key_base}"
            if status_key not in st.session_state:
                st.session_state[status_key] = ""

            if st.button("LinkedIn Connect", type="primary", use_container_width=True, key=f"lead_overview_linkedin_connect_btn_{key_base}"):
                missing = []
                if not lead_linkedin_default.strip():
                    missing.append("lead LinkedIn URL")

                if requests_today >= daily_limit:
                    st.warning(
                        f"Daily LinkedIn limit reached ({requests_today}/{daily_limit}). "
                        "Increase LINKEDIN_MAX_CONNECTIONS_PER_DAY tomorrow or update the limit in env."
                    )
                elif missing:
                    st.warning("Cannot start automation. Missing: " + ", ".join(missing))
                else:
                    draft_text = (
                        f"Hi, noticed your work at {raw_company_name}. We help teams with "
                        f"{selected_lead.get('product_description', 'key workflow improvements')}. "
                        "Would love to share a quick relevant update."
                    )

                    product_text = str(selected_lead.get("product_description", "") or "").lower()
                    if "digicom" in product_text or "digiexpense" in product_text:
                        draft_text = (
                            draft_text
                            + "\n\nBalaji Eepa"
                            + "\nDigi Delight Private Ltd"
                        )

                    ok, msg = _draft_linkedin_message_same_window(
                        target_profile_url=lead_linkedin_default,
                        draft_message=draft_text,
                    )
                    if ok:
                        st.session_state[count_key] = requests_today + 1
                        st.session_state[status_key] = "auto_drafted"
                        if "closed by user" in msg.lower():
                            st.info(msg)
                        else:
                            st.success(msg)
                    else:
                        st.session_state[status_key] = "automation_failed"
                        st.warning(
                            "Auto-draft failed before message-fill. "
                            f"Reason: {msg}. It will not open extra tabs now. Click LinkedIn 1 and I will tune selectors further if needed."
                        )

    with right:
        with st.container(border=True):
            st.markdown('<div class="lead-overview-right-marker" style="display:none;"></div>', unsafe_allow_html=True)
            st.markdown('<div class="lead-overview-section-title">Parsed Detail</div>', unsafe_allow_html=True)
            parsed_items = [
                ("Decision makers", format_list(selected_lead.get("decision_makers_found")) or "—"),
                ("Signals", format_list(selected_lead.get("signals_found")) or "—"),
                ("Emails found", format_list(selected_lead.get("emails_found")) or "—"),
                ("Job titles", format_list(selected_lead.get("job_titles_found")) or "—"),
                ("Source URL", selected_lead.get("source_url", "") or "—"),
                ("Imported from", selected_lead.get("source_file", "") or "—"),
            ]

            for label, value in parsed_items:
                st.markdown(f'<div class="lead-overview-item-label">{html.escape(str(label))}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="lead-overview-item-value">{html.escape(str(value))}</div>', unsafe_allow_html=True)

    components.html(
        """
        <script>
        (function () {
            function findContainer(marker) {
                if (!marker) return null;
                return (
                    marker.closest('[data-testid="stVerticalBlockBorderWrapper"]') ||
                    marker.closest('[data-testid="stVerticalBlock"]') ||
                    marker.parentElement
                );
            }

            function syncOverviewHeights() {
                const doc = window.parent && window.parent.document ? window.parent.document : document;
                const leftMarker = doc.querySelector('.lead-overview-left-marker');
                const rightMarker = doc.querySelector('.lead-overview-right-marker');
                if (!leftMarker || !rightMarker) return;

                const leftBox = findContainer(leftMarker);
                const rightBox = findContainer(rightMarker);
                if (!leftBox || !rightBox) return;

                rightBox.style.minHeight = '';
                const leftHeight = leftBox.getBoundingClientRect().height;
                rightBox.style.minHeight = Math.ceil(leftHeight) + 'px';
            }

            syncOverviewHeights();
            setTimeout(syncOverviewHeights, 80);
            setTimeout(syncOverviewHeights, 220);
            window.parent.addEventListener('resize', syncOverviewHeights);

            const doc = window.parent && window.parent.document ? window.parent.document : document;
            const observer = new MutationObserver(syncOverviewHeights);
            observer.observe(doc.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=0,
    )
