"""In-memory plan tracking — Claude Code-style checklist for multi-step work.

Plans live in `state["plan"]` and are replaced wholesale when the model calls
create_plan again. Only one step can be in_progress at a time.

The ENGINE keeps the ticks, not the model. The model used to tick steps with
its own tool, and every model tested spent whole round trips doing nothing
else (a third of the bill on one recorded run). Now the model only says which
step a tool call belongs to (the `step` argument on the work tools) and the
checklist follows: `begin` when the plan is made, `follow` on each tool call,
`finish` when the turn ends with an answer.
"""
from dataclasses import dataclass

VALID_STATUSES = ("pending", "in_progress", "completed", "blocked")


def _step_number(value) -> "int | None":
    """`step` as the model sent it -> int, or None. Models send 2, 2.0 and
    "2" interchangeably; True/False are not step numbers."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        whole = int(number)
    except (TypeError, ValueError, OverflowError):
        return None
    return whole if number == whole else None


@dataclass
class Step:
    title: str
    status: str = "pending"


class Plan:
    def __init__(self, titles, previous: "Plan | None" = None):
        # Coerce any non-string entries to str so a malformed model response
        # can't corrupt the plan; drop empties to avoid blank checkboxes.
        self.steps = [Step(str(t).strip()) for t in titles if str(t).strip()]
        # A REVISED plan keeps the ticks of the steps it carries over
        # unchanged, so re-planning halfway does not un-tick finished work.
        if previous is not None:
            done = {s.title for s in previous.steps if s.status == "completed"}
            for s in self.steps:
                if s.title in done:
                    s.status = "completed"

    # -- the engine's three moves -------------------------------------------
    def begin(self) -> None:
        """A plan was just made: its first unfinished step is in progress."""
        if any(s.status == "in_progress" for s in self.steps):
            return
        for s in self.steps:
            if s.status != "completed":
                s.status = "in_progress"
                return

    def follow(self, index) -> "bool | None":
        """A tool call said it belongs to step `index` (1-based).

        Returns None when `index` is not a usable step number, else whether
        the plan changed. Progress only moves FORWARD: reaching step N ticks
        every earlier step and puts N in progress; naming an earlier step
        again changes nothing (going back to touch up step 2 does not
        un-tick it)."""
        index = _step_number(index)
        if index is None or not 1 <= index <= len(self.steps):
            return None
        current = self.steps[index - 1]
        if current.status in ("completed", "in_progress"):
            return False
        for s in self.steps[: index - 1]:
            s.status = "completed"
        for s in self.steps:
            if s.status == "in_progress":
                s.status = "completed"
        current.status = "in_progress"
        return True

    def finish(self) -> bool:
        """The turn ended with an answer. If the step in progress is the LAST
        one, it is done; returns whether the plan changed. A turn that stops
        with later steps untouched keeps them unticked — the checklist never
        claims work nobody said was started."""
        active = [i for i, s in enumerate(self.steps) if s.status == "in_progress"]
        if not active:
            return False
        if any(s.status != "completed" for s in self.steps[active[-1] + 1:]):
            return False
        for i in active:
            self.steps[i].status = "completed"
        return True

    def current(self):
        """(1-based index, Step) of the step in progress, or None."""
        for i, s in enumerate(self.steps, 1):
            if s.status == "in_progress":
                return i, s
        return None

    def update(self, index, status):
        """1-based index. Returns an error string, or None on success.

        Only reached by a model that still calls the retired tick tool (an
        old chat replaying its own history); the engine uses begin / follow
        / finish."""
        if not isinstance(index, int):
            return f"`index` must be an integer (got {type(index).__name__})"
        if not 1 <= index <= len(self.steps):
            return f"step {index} out of range (have {len(self.steps)} step(s))"
        if status not in VALID_STATUSES:
            return f"`status` must be one of {VALID_STATUSES}, got {status!r}"
        # Auto-finish any other in-progress step when starting a new one — only
        # one step is in_progress at a time, mirroring Claude Code's checklist.
        if status == "in_progress":
            for s in self.steps:
                if s.status == "in_progress":
                    s.status = "completed"
        self.steps[index - 1].status = status
        return None

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        """Rebuild from the wire form — used when a session is restored."""
        plan = cls([st.get("title", "") for st in (data or {}).get("steps", [])])
        for i, st in enumerate((data or {}).get("steps", [])):
            if i < len(plan.steps) and st.get("status") in VALID_STATUSES:
                plan.steps[i].status = st["status"]
        return plan

    def to_dict(self):
        """Wire form for Event.PLAN_UPDATED — the UI owns presentation.

        This replaces the CLI's render(): the plan used to draw itself with
        rich markup, which is precisely the coupling that made a second
        frontend impossible.
        """
        return {
            "steps": [
                {"index": i, "title": s.title, "status": s.status}
                for i, s in enumerate(self.steps, 1)
            ],
            "summary": self.summary(),
            "done": sum(1 for s in self.steps if s.status == "completed"),
            "total": len(self.steps),
            "complete": self.is_complete(),
        }

    def summary(self):
        done = sum(1 for s in self.steps if s.status == "completed")
        return f"({done}/{len(self.steps)} done)"

    def is_complete(self):
        """True when every step is completed (an empty plan is not 'complete')."""
        return bool(self.steps) and all(s.status == "completed" for s in self.steps)

    def incomplete(self):
        """[(1-based index, Step)] for every step not yet completed."""
        return [(i, s) for i, s in enumerate(self.steps, 1) if s.status != "completed"]

    def reminder_text(self):
        """Plain-text list of the steps still outstanding, for re-grounding the
        model mid-turn. One line per step, with a status marker for anything
        that isn't a plain pending step."""
        lines = []
        for i, s in self.incomplete():
            mark = {
                "in_progress": " (in progress)",
                "blocked": " (blocked)",
            }.get(s.status, "")
            lines.append(f"  {i}. {s.title}{mark}")
        return "\n".join(lines)
