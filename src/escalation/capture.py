"""The recorder installed into the page before a human takes over.

Injected twice on purpose: `add_init_script` so it survives every navigation the operator
makes, and `evaluate` so it is also live in the document that is already loaded. Without the
second, nothing on the current screen is recorded, which is usually the screen that matters.

The array lives in sessionStorage rather than on `window`, because `window` is destroyed on
navigation and a handoff that involves clicking through three screens would keep only the
last. sessionStorage is per tab and per origin, which is exactly the scope of one handoff.

WHAT IS DELIBERATELY NOT RECORDED: the value of anything typed. A change event carries the
new value and this script drops it on the floor, keeping only which field changed. Invariant 6
applies to a person's keystrokes exactly as it applies to a model's. See DECISIONS.md 0033.
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
    } catch (e) { /* a full or blocked store must not break the page a human is using */ }
  };

  // Field identity, never field content. name, then id, then the label text beside it.
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

  // 'change' and not 'input': one entry per field the human finished with, rather than one
  // per keystroke. The value is read nowhere in this handler.
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
    """Arm the recorder for every future document and for the one already loaded."""
    page.add_init_script(RECORDER_JS)
    page.evaluate(RECORDER_JS)


def drain(page: Any) -> Any:
    """Read what was recorded, then clear it so a second handoff starts empty."""
    try:
        captured = page.evaluate(READ_JS)
        page.evaluate(CLEAR_JS)
    except Exception:  # noqa: BLE001
        # The recorder can be lost: a cross origin navigation, a page that clears storage, an
        # operator who closed the tab. The before and after aria snapshots are the fallback
        # evidence, which is exactly why they are captured separately.
        return []
    return captured
