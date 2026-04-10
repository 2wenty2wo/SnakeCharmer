"""Accessibility tests for SnakeCharmer Web UI.

These tests verify WCAG 2.1 AA compliance including:
- Keyboard navigation
- Focus management
- ARIA labels
- Color contrast
- Screen reader support
"""

import pytest

playwright = pytest.importorskip("playwright.sync_api")
Page = playwright.Page
expect = playwright.expect


def test_skip_to_content_link_exists(page: Page, live_server_url: str):
    """Verify skip-to-content link is present for keyboard users."""
    page.goto(f"{live_server_url}/")
    
    skip_link = page.locator(".skip-link")
    expect(skip_link).to_be_visible()
    expect(skip_link).to_have_attribute("href", "#main-content")


def test_skip_to_content_link_works(page: Page, live_server_url: str):
    """Verify skip-to-content link moves focus to main content."""
    page.goto(f"{live_server_url}/")
    
    # Tab to skip link
    page.keyboard.press("Tab")
    
    skip_link = page.locator(".skip-link")
    expect(skip_link).to_be_focused()
    
    # Press Enter to activate
    page.keyboard.press("Enter")
    
    # URL should change to include hash
    expect(page).to_have_url(f"{live_server_url}/#main-content")
    
    # Main content should be visible (scrolled into view)
    main_content = page.locator("#main-content")
    expect(main_content).to_be_visible()


def test_all_interactive_elements_are_focusable(page: Page, live_server_url: str):
    """Verify all buttons and links can be focused via keyboard."""
    page.goto(f"{live_server_url}/")
    
    # Get all interactive elements
    buttons = page.locator("button, a[href], input, select, textarea")
    count = buttons.count()
    
    assert count > 0, "Page should have interactive elements"
    
    # Tab through each element and verify it receives focus
    for i in range(min(count, 10)):  # Check first 10 to avoid infinite loops
        page.keyboard.press("Tab")
        
        # Get currently focused element
        focused = page.evaluate("() => document.activeElement?.tagName")
        assert focused != "BODY", f"Element {i} should be focusable, not skip to body"


def test_buttons_have_accessible_names(page: Page, live_server_url: str):
    """Verify all buttons have accessible names (text or aria-label)."""
    page.goto(f"{live_server_url}/")
    
    buttons = page.locator("button")
    count = buttons.count()
    
    for i in range(count):
        button = buttons.nth(i)
        
        # Check for text content or aria-label
        text = button.inner_text().strip()
        aria_label = button.get_attribute("aria-label") or ""
        title = button.get_attribute("title") or ""
        
        # Should have at least one accessible name source
        assert text or aria_label or title, \
            f"Button {i} lacks accessible name (no text, aria-label, or title)"


def test_images_have_alt_text(page: Page, live_server_url: str):
    """Verify all images have alt attributes."""
    page.goto(f"{live_server_url}/")
    
    images = page.locator("img")
    count = images.count()
    
    for i in range(count):
        img = images.nth(i)
        alt = img.get_attribute("alt")
        
        # All images should have alt (can be empty for decorative)
        assert alt is not None, f"Image {i} lacks alt attribute"


def test_icon_buttons_have_aria_labels(page: Page, live_server_url: str):
    """Verify icon-only buttons have aria-label for screen readers."""
    page.goto(f"{live_server_url}/pending")
    
    # Look for icon buttons (buttons with only SVG/icon, no text)
    icon_buttons = page.locator("button[class*='icon'], .btn-icon-approve, .btn-icon-reject")
    
    if icon_buttons.count() > 0:
        for i in range(icon_buttons.count()):
            button = icon_buttons.nth(i)
            aria_label = button.get_attribute("aria-label")
            title = button.get_attribute("title")
            
            assert aria_label or title, \
                f"Icon button {i} should have aria-label or title"


def test_form_labels_are_associated(page: Page, live_server_url: str):
    """Verify form inputs have associated labels."""
    page.goto(f"{live_server_url}/config/trakt")
    
    # Check inputs inside field-label wrappers
    inputs = page.locator(".field-label input, .field-label select, .field-label textarea")
    count = inputs.count()
    
    missing_labels = []
    
    for i in range(count):
        input_el = inputs.nth(i)
        
        # Skip hidden inputs
        input_type = input_el.get_attribute("type")
        if input_type == "hidden":
            continue
        
        # Check for explicit label association
        input_id = input_el.get_attribute("id")
        aria_labelled_by = input_el.get_attribute("aria-labelledby")
        aria_label = input_el.get_attribute("aria-label")
        placeholder = input_el.get_attribute("placeholder")
        
        # Get parent label text
        parent = input_el.locator("xpath=ancestor::label").first
        has_parent_label = parent.count() > 0 and parent.is_visible()
        
        # Input should have some form of labeling
        if not (has_parent_label or aria_label or aria_labelled_by or placeholder):
            missing_labels.append(f"Input {i} (type={input_type})")
    
    assert len(missing_labels) == 0, f"Inputs lack associated labels: {missing_labels}"


def test_color_contrast_meets_wcag_aa(page: Page, live_server_url: str):
    """Verify text color contrast meets WCAG AA (4.5:1 for normal text)."""
    page.goto(f"{live_server_url}/")
    
    # Check body text contrast
    body = page.locator("body")
    bg_color = body.evaluate("el => getComputedStyle(el).backgroundColor")
    text_color = body.evaluate("el => getComputedStyle(el).color")
    
    # Parse RGB values
    def parse_rgb(color_str):
        import re
        match = re.search(r'rgb\((\d+),\s*(\d+),\s*(\d+)\)', color_str)
        if match:
            return tuple(int(x) for x in match.groups())
        return (0, 0, 0)
    
    def luminance(rgb):
        r, g, b = [x / 255.0 for x in rgb]
        r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
        g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
        b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    
    def contrast_ratio(color1, color2):
        lum1 = luminance(parse_rgb(color1))
        lum2 = luminance(parse_rgb(color2))
        lighter = max(lum1, lum2)
        darker = min(lum1, lum2)
        return (lighter + 0.05) / (darker + 0.05)
    
    ratio = contrast_ratio(bg_color, text_color)
    
    assert ratio >= 4.5, \
        f"Body text contrast ratio {ratio:.2f} is below WCAG AA (4.5:1)"


def test_focus_indicators_are_visible(page: Page, live_server_url: str):
    """Verify focus indicators are clearly visible."""
    page.goto(f"{live_server_url}/")
    
    # Find a button and focus it
    button = page.locator("button").first
    button.focus()
    
    # Check for outline or box-shadow indicating focus
    outline = button.evaluate("el => getComputedStyle(el).outline")
    box_shadow = button.evaluate("el => getComputedStyle(el).boxShadow")
    
    has_focus_indicator = (
        outline != "none" and outline != "0px" or
        box_shadow != "none"
    )
    
    assert has_focus_indicator, "Button should have visible focus indicator"


def test_page_titles_are_descriptive(page: Page, live_server_url: str):
    """Verify all pages have descriptive titles."""
    pages = [
        ("/", "Dashboard"),
        ("/config/trakt", "Trakt"),
        ("/config/medusa", "Medusa"),
        ("/config/sync", "Sync"),
        ("/config/health", "Health"),
        ("/config/notify", "Notification"),
        ("/sync/history", "History"),
        ("/pending", "Pending"),
        ("/library", "Library"),
    ]
    
    for path, expected in pages:
        page.goto(f"{live_server_url}{path}")
        title = page.title()
        
        assert expected in title, \
            f"Page {path} title should contain '{expected}', got: {title}"
        assert "SnakeCharmer" in title, \
            f"Page {path} title should contain 'SnakeCharmer', got: {title}"


def test_aria_current_for_active_navigation(page: Page, live_server_url: str):
    """Verify active navigation link has aria-current attribute."""
    page.goto(f"{live_server_url}/")
    
    # Active link should have aria-current="page"
    active_link = page.locator("nav a.active").first
    
    if active_link.is_visible():
        aria_current = active_link.get_attribute("aria-current")
        assert aria_current == "page", \
            "Active nav link should have aria-current='page'"


def test_status_messages_use_aria_live(page: Page, live_server_url: str):
    """Verify dynamic status messages use aria-live regions."""
    page.goto(f"{live_server_url}/")
    
    # Check result banner has aria-live
    banner = page.locator("#result-banner")
    aria_live = banner.get_attribute("aria-live")
    assert aria_live in ["polite", "assertive"]


def test_keyboard_escape_closes_mobile_menu(page: Page, live_server_url: str):
    """Verify Escape key closes mobile menu."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{live_server_url}/")
    
    # Open mobile menu
    menu_toggle = page.locator("#mobile-menu-toggle")
    menu_toggle.click()
    
    # Menu should be open
    sidebar = page.locator("#sidebar")
    sidebar_classes = sidebar.get_attribute("class")
    assert "open" in sidebar_classes
    
    # Press Escape
    page.keyboard.press("Escape")
    
    # Menu should be closed
    sidebar_classes = sidebar.get_attribute("class")
    assert "open" not in sidebar_classes


def test_form_validation_errors_are_accessible(page: Page, live_server_url: str):
    """Verify form validation errors are announced to screen readers."""
    page.goto(f"{live_server_url}/config/trakt")
    
    # Clear required field and submit
    client_id = page.locator("input[name='client_id']")
    client_id.fill("")
    
    # Try to submit
    page.locator("button[type='submit']").click()
    
    # Check for error banner with role="alert"
    error_banner = page.locator(".banner.error, [role='alert']").first
    
    if error_banner.is_visible():
        # Error should be announced
        role = error_banner.get_attribute("role")
        assert role in ["alert", "status"], \
            "Error messages should have appropriate ARIA role"


@pytest.fixture
def live_server_url(tmp_path):
    """Create a live server for Playwright tests."""
    import threading
    import time
    import uvicorn
    
    from app.config import AppConfig, HealthConfig, MedusaConfig, SyncConfig, TraktConfig, TraktSource, WebUIConfig
    from app.webui import ConfigHolder, create_app
    from app.webui.config_io import save_app_config
    
    config = AppConfig(
        trakt=TraktConfig(
            client_id="test_id",
            client_secret="test_secret",
            username="testuser",
            sources=[TraktSource(type="trending")],
            limit=50,
        ),
        medusa=MedusaConfig(url="http://localhost:8081", api_key="test_key"),
        sync=SyncConfig(),
        health=HealthConfig(),
        webui=WebUIConfig(),
        config_dir=str(tmp_path),
    )
    
    config_path = str(tmp_path / "config.yaml")
    save_app_config(config, config_path)
    
    holder = ConfigHolder(config=config, config_path=config_path)
    app = create_app(holder)
    
    import random
    port = random.randint(10000, 65000)
    
    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    
    time.sleep(2)
    
    yield f"http://127.0.0.1:{port}"
