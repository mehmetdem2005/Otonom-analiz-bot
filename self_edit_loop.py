"""
self_edit_loop.py
-----------------
Allows the autonomous agent to read and update its own plan file
(plan_ve_durum_listesi.txt).

Functions
---------
read_plan        -- parse the plan file into structured dicts
mark_done        -- mark a pending (❌) item as done (✅)
add_task         -- append a new ❌ task, optionally under a section header
get_pending      -- return list of pending task texts
get_summary      -- return total/done/pending/ratio_done statistics
run_self_edit    -- async: fuzzy-match objective → auto-mark related items done
"""

import os
import asyncio

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

_DEFAULT_PLAN = os.path.join(os.path.dirname(__file__), "plan_ve_durum_listesi.txt")

DONE_MARKER = "✅"
PENDING_MARKER = "❌"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve(plan_path):
    return plan_path if plan_path is not None else _DEFAULT_PLAN


def _read_lines(plan_path):
    """Return list of raw lines (with newlines stripped)."""
    path = _resolve(plan_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().splitlines()
    except FileNotFoundError:
        return []


def _write_lines(lines, plan_path):
    """Overwrite the plan file with *lines* (no trailing newlines expected)."""
    path = _resolve(plan_path)
    content = "\n".join(lines) + "\n"
    with open(path, "r+", encoding="utf-8") as fh:
        if _HAS_FCNTL:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            fh.write(content)
            fh.truncate()
        finally:
            if _HAS_FCNTL:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _classify(line: str) -> str:
    stripped = line.strip()
    if DONE_MARKER in stripped:
        return "done"
    if PENDING_MARKER in stripped:
        return "pending"
    return "other"


def _extract_text(line: str) -> str:
    """Return the task text, removing leading markers and whitespace."""
    text = line.strip()
    for marker in (DONE_MARKER, PENDING_MARKER):
        text = text.replace(marker, "")
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_plan(plan_path=None) -> list:
    """
    Parse the plan file and return a list of dicts:
        {"line": int, "status": "done"|"pending"|"other", "text": str}

    Line numbers are 1-based. Returns an empty list if the file is not found.
    """
    lines = _read_lines(plan_path)
    result = []
    for idx, raw in enumerate(lines, start=1):
        result.append({
            "line": idx,
            "status": _classify(raw),
            "text": _extract_text(raw),
        })
    return result


def mark_done(item_text: str, plan_path=None) -> bool:
    """
    Find the first line that contains *item_text* AND has a ❌ marker,
    replace ❌ with ✅, and persist the change.

    Returns True if a line was found and changed, False otherwise.
    """
    lines = _read_lines(plan_path)
    if not lines:
        return False

    changed = False
    for i, line in enumerate(lines):
        if PENDING_MARKER in line and item_text in line:
            lines[i] = line.replace(PENDING_MARKER, DONE_MARKER, 1)
            changed = True
            break

    if changed:
        _write_lines(lines, plan_path)
    return changed


def add_task(task_text: str, section: str = None, plan_path=None) -> bool:
    """
    Append a new ❌ task line.

    If *section* is given, the line is inserted directly after the first line
    that contains *section* as a substring (case-insensitive).  If the section
    is not found the task is appended at the end.

    Returns True on success, False if the file was not found and could not be
    written (e.g. directory missing).
    """
    path = _resolve(plan_path)
    lines = _read_lines(plan_path)

    new_line = f"{PENDING_MARKER} {task_text}"

    if section:
        section_lower = section.lower()
        insert_at = None
        for i, line in enumerate(lines):
            if section_lower in line.lower():
                insert_at = i + 1
                break
        if insert_at is not None:
            lines.insert(insert_at, new_line)
        else:
            lines.append(new_line)
    else:
        lines.append(new_line)

    try:
        # If file does not exist yet, create it.
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        else:
            _write_lines(lines, plan_path)
        return True
    except OSError:
        return False


def get_pending(plan_path=None) -> list:
    """
    Return a list of text strings for all pending (❌) tasks.
    """
    return [
        item["text"]
        for item in read_plan(plan_path)
        if item["status"] == "pending"
    ]


def get_summary(plan_path=None) -> dict:
    """
    Return a summary dict::

        {"total": N, "done": N, "pending": N, "ratio_done": float}

    Only lines with ✅ or ❌ markers are counted (``status != "other"``).
    """
    items = [i for i in read_plan(plan_path) if i["status"] != "other"]
    total = len(items)
    done = sum(1 for i in items if i["status"] == "done")
    pending = sum(1 for i in items if i["status"] == "pending")
    ratio = done / total if total > 0 else 0.0
    return {
        "total": total,
        "done": done,
        "pending": pending,
        "ratio_done": round(ratio, 4),
    }


# ---------------------------------------------------------------------------
# Async self-edit
# ---------------------------------------------------------------------------

def _word_overlap(a: str, b: str) -> float:
    """
    Jaccard-like word overlap between two strings.
    Splits on whitespace, lower-cases tokens.
    Returns a float in [0, 1].
    """
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    union = len(a_words | b_words)
    if union == 0:
        return 0.0
    return len(a_words & b_words) / union


async def run_self_edit(objective: str, plan_path=None) -> dict:
    """
    Given the agent's current *objective* string, auto-mark related pending
    plan items as done by fuzzy matching (word overlap ≥ 0.4).

    Returns::

        {"marked": [<task_text>, ...], "summary": <get_summary() result>}
    """
    pending_texts = await asyncio.get_event_loop().run_in_executor(
        None, get_pending, plan_path
    )

    marked = []
    for text in pending_texts:
        score = _word_overlap(objective, text)
        if score >= 0.4:
            success = await asyncio.get_event_loop().run_in_executor(
                None, mark_done, text, plan_path
            )
            if success:
                marked.append(text)

    summary = await asyncio.get_event_loop().run_in_executor(
        None, get_summary, plan_path
    )
    return {"marked": marked, "summary": summary}


# ---------------------------------------------------------------------------
# __main__ — print plan summary
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    summary = get_summary()
    print("Plan Summary")
    print("============")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    pending = get_pending()
    if pending:
        print(f"\nPending tasks ({len(pending)}):")
        for task in pending:
            print(f"  {PENDING_MARKER} {task}")
    else:
        print("\nNo pending tasks.")
