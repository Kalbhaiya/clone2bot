import json
import re
from typing import Optional

from config import (
    CAPTION_ADD_START,
    CAPTION_ADD_END,
    CAPTION_REPLACE,
    CAPTION_REMOVE,
    CAPTION_REMOVE_LINES,
    caption_modifications_enabled,
)


def apply_dynamic_rules(text: Optional[str]) -> Optional[str]:
    """Apply settings-based caption rules (replace + skip). Returns None = skip message."""
    try:
        from utils.settings import apply_caption_rules
        return apply_caption_rules(text)
    except Exception:
        return text


def modify_caption(original: Optional[str]) -> Optional[str]:
    """
    Apply all configured caption modifications in order.

    Processing order:
    1. CAPTION_REMOVE_LINES — remove lines containing keywords
    2. CAPTION_REMOVE       — remove specific words/phrases
    3. CAPTION_REPLACE      — replace pairs from JSON dict
    4. CAPTION_ADD_START    — prepend text
    5. CAPTION_ADD_END      — append text

    Args:
        original: Original caption string or None

    Returns:
        Modified caption string, or None if result is empty
    """
    if not caption_modifications_enabled():
        return original

    # If original is None and no add operations, return None
    if original is None:
        has_add = bool(CAPTION_ADD_START.strip() or CAPTION_ADD_END.strip())
        if not has_add:
            return None

    text = original or ""

    # ─── Step 1: Remove lines containing keywords ─────────────
    if CAPTION_REMOVE_LINES.strip():
        keywords = [k.strip() for k in CAPTION_REMOVE_LINES.split(",") if k.strip()]
        if keywords:
            lines = text.split("\n")
            filtered = [
                line for line in lines
                if not any(kw in line for kw in keywords)
            ]
            text = "\n".join(filtered)

    # ─── Step 2: Remove words/phrases ────────────────────────
    if CAPTION_REMOVE.strip():
        removals = [r.strip() for r in CAPTION_REMOVE.split(",") if r.strip()]
        for removal in removals:
            text = text.replace(removal, "")

    # ─── Step 3: Replace pairs ────────────────────────────────
    if CAPTION_REPLACE.strip():
        try:
            pairs = json.loads(CAPTION_REPLACE)
            if isinstance(pairs, dict):
                for old, new in pairs.items():
                    text = text.replace(str(old), str(new))
        except (json.JSONDecodeError, Exception):
            pass

    # ─── Step 4: Add to start ─────────────────────────────────
    if CAPTION_ADD_START.strip():
        text = CAPTION_ADD_START + text

    # ─── Step 5: Add to end ───────────────────────────────────
    if CAPTION_ADD_END.strip():
        text = text + CAPTION_ADD_END

    # ─── Cleanup ──────────────────────────────────────────────
    result = text.strip()

    if not result:
        return None

    return result


def get_caption_summary() -> str:
    """
    Return human-readable summary of active caption modifications.

    Returns:
        Formatted string listing all active/inactive modifications
    """
    parts = []

    if CAPTION_ADD_START.strip():
        preview = CAPTION_ADD_START[:20] + ("..." if len(CAPTION_ADD_START) > 20 else "")
        parts.append(f"✅ Add Start: '{preview}'")
    else:
        parts.append("⏭️ Add Start: OFF")

    if CAPTION_ADD_END.strip():
        preview = CAPTION_ADD_END[:20] + ("..." if len(CAPTION_ADD_END) > 20 else "")
        parts.append(f"✅ Add End: '{preview}'")
    else:
        parts.append("⏭️ Add End: OFF")

    if CAPTION_REPLACE.strip():
        try:
            pairs = json.loads(CAPTION_REPLACE)
            count = len(pairs) if isinstance(pairs, dict) else 0
            parts.append(f"✅ Replace: {count} pairs")
        except Exception:
            parts.append("❌ Replace: Invalid JSON")
    else:
        parts.append("⏭️ Replace: OFF")

    if CAPTION_REMOVE.strip():
        words = [w.strip() for w in CAPTION_REMOVE.split(",") if w.strip()]
        parts.append(f"✅ Remove: {len(words)} words")
    else:
        parts.append("⏭️ Remove: OFF")

    if CAPTION_REMOVE_LINES.strip():
        kws = [k.strip() for k in CAPTION_REMOVE_LINES.split(",") if k.strip()]
        parts.append(f"✅ Remove Lines: {len(kws)} keywords")
    else:
        parts.append("⏭️ Remove Lines: OFF")

    return " | ".join(parts)