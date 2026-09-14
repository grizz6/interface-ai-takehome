"""A small script put into the page before a person takes over, to record what they do.

It is added twice: with `add_init_script` so it survives every page the operator visits, and
with `evaluate` so it also runs on the page already open. Without the second, nothing on the
current screen is recorded, and that is usually the one that matters.

Actions are stored in sessionStorage, not on `window`, because `window` is wiped on every
navigation and a handoff across three pages would only keep the last one.

It never records what was typed. A change event carries the new value, and this script ignores
it and only keeps which field changed. See DECISIONS.md 0033.
"""
from __future__ import annotations

from typing import Any, Final

STORAGE_KEY: Final[str] = "__interface_ai_human_actions__"

RECORDER_JS: Final[str] = """
(() => {
  const KEY = "%s";
  if (window.__interfaceAiRecorderInstalled) return;
  window.__interfaceAiRecorderInstalled = true;

  const read = () => {
    try { return JSON.parse(sessionStorage.getItem(KEY) || "[]"); }
    catch (e) { return []; }
  };
  const push = (entry) => {
    try {
      const all = read();
      all.push(Object.assign({ at: new Date().toISOString() }, entry));
      sessionStorage.setItem(KEY, JSON.stringify(all.slice(-200)));
    } catch (e) { /* never break the page the person is using */ }
  };

  // Which field, never what is in it: name, then id, then aria-label.
  const identify = (el) => {
    if (!el) return null;
    return el.getAttribute("name") || el.id || (el.getAttribute("aria-label") || null);
  };

  push({ kind: "navigate", url: location.href });

  document.addEventListener("click", (ev) => {
    const el = ev.target instanceof Element ? ev.target.closest("a,button,input,[role]") : null;
    if (!el) return;
    const label = el.tagName === "INPUT" ? (el.getAttribute("value") || "") : (el.innerText || "");
    push({
      kind: "click",
      tag: el.tagName.toLowerCase(),
      text: label.trim().slice(0, 120) || null,
      element_id: el.id || null,
      url: location.href
    });
  }, true);

  // 'change' rather than 'input', so there is one entry per field, not one per keystroke.
  // The value is never read here.
  document.addEventListener("change", (ev) => {
    const el = ev.target;
    if (!el || !("tagName" in el)) return;
    push({
      kind: "field_change",
      tag: el.tagName.toLowerCase(),
      field: identify(el),
      element_id: el.id || null,
      url: location.href
    });
  }, true);
})();
""" % STORAGE_KEY

READ_JS: Final[str] = """
(() => {
  try { return JSON.parse(sessionStorage.getItem("%s") || "[]"); }
  catch (e) { return []; }
})()
""" % STORAGE_KEY

CLEAR_JS: Final[str] = """
(() => { try { sessionStorage.removeItem("%s"); } catch (e) {} })()
""" % STORAGE_KEY


def install(page: Any) -> None:
    """Install the recorder on the current page and every page after it."""
    page.add_init_script(RECORDER_JS)
    page.evaluate(RECORDER_JS)


def drain(page: Any) -> Any:
    """Read what was recorded, then clear it so a second handoff starts empty."""
    try:
        captured = page.evaluate(READ_JS)
        page.evaluate(CLEAR_JS)
    except Exception:  # noqa: BLE001
        # The recorder can get lost: navigating to another origin, a page clearing storage,
        # or the operator closing the tab. The before and after snapshots are kept separately
        # for that reason.
        return []
    return captured
