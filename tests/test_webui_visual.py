"""Visual and layout tests for SnakeCharmer Web UI.

These tests verify DESIGN.md compliance including:
- Green Deck color scheme
- Responsive layout at multiple breakpoints
- Component styling consistency
"""

import pytest

playwright = pytest.importorskip("playwright.sync_api")
Page = playwright.Page
expect = playwright.expect


def test_dashboard_renders_with_green_deck_colors(page: Page, live_server_url: str):
    """Verify dashboard follows Green Deck design system colors."""
    page.goto(f"{live_server_url}/")
    
    # Check background color (Void Black #121212)
    body = page.locator("body")
    expect(body).to_have_css("background-color", "rgb(18, 18, 18)")
    
    # Check primary button color (Spotify Green #1DB954)
    sync_button = page.locator("button:has-text('Sync Now')").first
    expect(sync_button).to_have_css("background-color", "rgb(29, 185, 84)")
    
    # Check card surface color (#181818)
    card = page.locator(".card").first
    expect(card).to_have_css("background-color", "rgb(24, 24, 24)")


def test_sidebar_is_black(page: Page, live_server_url: str):
    """Verify sidebar has correct black background."""
    page.goto(f"{live_server_url}/")
    
    sidebar = page.locator(".sidebar")
    expect(sidebar).to_have_css("background-color", "rgb(0, 0, 0)")


def test_buttons_are_pill_shaped(page: Page, live_server_url: str):
    """Verify buttons have pill shape (border-radius: 9999px)."""
    page.goto(f"{live_server_url}/")
    
    primary_button = page.locator(".btn-primary").first
    expect(primary_button).to_have_css("border-radius", "9999px")
    
    secondary_button = page.locator(".btn-secondary").first
    expect(secondary_button).to_have_css("border-radius", "9999px")


def test_cards_have_hover_effect(page: Page, live_server_url: str):
    """Verify cards have lift effect on hover."""
    page.goto(f"{live_server_url}/")
    
    card = page.locator(".card").first
    
    # Get initial state
    initial_transform = card.evaluate("el => getComputedStyle(el).transform")
    initial_box_shadow = card.evaluate("el => getComputedStyle(el).boxShadow")
    
    # Hover over card
    card.hover()
    
    # Check that hover state has different transform/box-shadow
    # (The exact values depend on CSS, but they should change)
    page.wait_for_timeout(300)  # Wait for transition
    
    hover_transform = card.evaluate("el => getComputedStyle(el).transform")
    hover_box_shadow = card.evaluate("el => getComputedStyle(el).boxShadow")
    
    # Transform should change on hover (translateY)
    assert initial_transform != hover_transform or hover_transform != "none", \
        "Card should have hover transform effect"


def test_mobile_hamburger_menu_appears(page: Page, live_server_url: str):
    """Verify mobile menu toggle appears at small screen sizes."""
    # Desktop view - hamburger should be hidden
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(f"{live_server_url}/")
    
    menu_toggle = page.locator("#mobile-menu-toggle")
    expect(menu_toggle).not_to_be_visible()
    
    # Mobile view - hamburger should appear
    page.set_viewport_size({"width": 375, "height": 812})
    page.reload()
    
    expect(menu_toggle).to_be_visible()


def test_mobile_menu_opens_and_closes(page: Page, live_server_url: str):
    """Verify mobile menu can be opened and closed."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{live_server_url}/")
    
    sidebar = page.locator("#sidebar")
    overlay = page.locator("#sidebar-overlay")
    menu_toggle = page.locator("#mobile-menu-toggle")
    
    # Initially sidebar should be hidden (off-screen)
    sidebar_classes = sidebar.get_attribute("class")
    assert "open" not in sidebar_classes
    
    # Click hamburger menu
    menu_toggle.click()
    
    # Sidebar should be open
    sidebar_classes = sidebar.get_attribute("class")
    assert "open" in sidebar_classes
    overlay_classes = overlay.get_attribute("class")
    assert "active" in overlay_classes
    
    # Click overlay to close
    overlay.click()
    
    # Sidebar should be closed
    sidebar_classes = sidebar.get_attribute("class")
    assert "open" not in sidebar_classes


def test_all_pages_render_without_console_errors(page: Page, live_server_url: str):
    """Verify all pages load without JavaScript errors."""
    pages = [
        "/",
        "/config/trakt",
        "/config/medusa",
        "/config/sync",
        "/config/health",
        "/config/notify",
        "/sync/history",
        "/pending",
        "/library",
    ]
    
    console_errors = []
    
    def handle_console_error(msg):
        if msg.type == "error" and "favicon.ico" not in msg.text:
            console_errors.append(f"{msg.type}: {msg.text}")
    
    page.on("console", handle_console_error)
    
    for path in pages:
        console_errors.clear()
        page.goto(f"{live_server_url}{path}")
        page.wait_for_load_state("networkidle")
        
        assert len(console_errors) == 0, \
            f"Console errors on {path}: {console_errors}"


def test_responsive_layout_at_breakpoints(page: Page, live_server_url: str):
    """Verify layout adapts correctly at different screen sizes."""
    breakpoints = [
        (1280, 720, "desktop"),
        (1024, 768, "tablet"),
        (768, 1024, "tablet-portrait"),
        (375, 812, "mobile"),
    ]
    
    for width, height, name in breakpoints:
        page.set_viewport_size({"width": width, "height": height})
        page.goto(f"{live_server_url}/")
        
        # Verify content is visible
        main_content = page.locator(".main-content")
        expect(main_content).to_be_visible()
        
        # Verify no horizontal scroll (content fits)
        body_scroll_width = page.evaluate("() => document.body.scrollWidth")
        viewport_width = page.evaluate("() => window.innerWidth")
        assert body_scroll_width <= viewport_width, \
            f"Horizontal overflow at {name} breakpoint ({width}px)"


def test_empty_states_use_lucide_icons(page: Page, live_server_url: str):
    """Verify empty states use Lucide icons instead of emoji."""
    # Pending page (when empty)
    page.goto(f"{live_server_url}/pending")
    
    empty_state = page.locator(".empty-state")
    if empty_state.is_visible():
        # Check for Lucide icon (svg with data-lucide attribute)
        lucide_icon = empty_state.locator("[data-lucide], svg")
        assert lucide_icon.count() > 0, \
            "Empty state should use Lucide icon, not emoji"


def test_form_inputs_have_focus_states(page: Page, live_server_url: str):
    """Verify form inputs have visible focus indicators."""
    page.goto(f"{live_server_url}/config/trakt")
    
    input_field = page.locator("input[name='client_id']").first
    
    # Focus the input
    input_field.focus()
    
    # Check for focus indicator (box-shadow or outline)
    box_shadow = input_field.evaluate("el => getComputedStyle(el).boxShadow")
    outline = input_field.evaluate("el => getComputedStyle(el).outline")
    
    has_focus_indicator = (
        "rgb(29, 185, 84)" in box_shadow or  # Green glow
        outline != "none"
    )
    
    assert has_focus_indicator, "Input should have visible focus indicator"


def test_status_badges_have_correct_colors(page: Page, live_server_url: str):
    """Verify status badges use correct semantic colors."""
    page.goto(f"{live_server_url}/sync/history")
    
    # Check if success badge exists and has green color
    success_badge = page.locator(".status-ok").first
    if success_badge.is_visible():
        color = success_badge.evaluate("el => getComputedStyle(el).color")
        # Should be green (rgba for #1DB954)
        assert "29, 185, 84" in color or "rgb(29, 185, 84)" in color, \
            "Success badge should be green"


def test_typography_uses_dm_sans(page: Page, live_server_url: str):
    """Verify DM Sans font is applied throughout."""
    page.goto(f"{live_server_url}/")
    
    body = page.locator("body")
    font_family = body.evaluate("el => getComputedStyle(el).fontFamily")
    
    assert "DM Sans" in font_family, \
        f"Body should use DM Sans font, got: {font_family}"


@pytest.fixture
def live_server_url(tmp_path):
    """Create a live server for Playwright tests."""
    import threading
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
    
    server = None
    
    def run_server():
        nonlocal server
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    
    import time
    time.sleep(2)  # Wait for server to start
    
    yield f"http://127.0.0.1:{port}"
