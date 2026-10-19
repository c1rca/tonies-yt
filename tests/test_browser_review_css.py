"""Guards for defects first reproduced with real pointer/queued-upload browser QA."""
import re

from page_sources import page_css


def _rule(selector):
    match = re.search(re.escape(selector) + r'\s*\{([^}]+)\}', page_css())
    assert match, selector
    return dict(re.findall(r'([\w-]+)\s*:\s*([^;]+);', match[1]))


def test_open_settings_lifts_its_header_stacking_context_above_sticky_rail():
    # view-transition-name creates the header context: lifting only the child menu
    # cannot place it above the mobile sticky target (z=10) or selector (z=30).
    header = _rule('.app-header:has(.app-settings[open])')
    assert header['position'].strip() == 'relative'
    assert 30 < int(header['z-index']) < 35  # dialogs stay above the menu


def test_queued_upload_does_not_dim_all_descendant_controls():
    # Child opacity:1 cannot undo an ancestor opacity:.7. Keep the queue label and
    # disabled Add state as the feedback, without washing out readable controls.
    queued = _rule('.file.queued-upload')
    assert float(queued.get('opacity', '1')) == 1
