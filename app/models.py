"""
Pydantic data models and default settings.

Storage schemas are deliberately minimal and human-readable:

* employees.json : list[Employee]  -> names AND roles live ONLY here
* shifts.json    : list[Shift]      -> id, employee_id, clock_in, clock_out,
  plus a snapshot of which role was worked (role_id/role_title/department/
  hourly_rate, taken at clock-in) and the computed "hours" once clocked out.
  The snapshot means a later change to a role's rate never rewrites the pay
  history of shifts already worked under the old rate.
* adjustments.json (per week, optional) : per-shift break overrides, keyed by
  shift id. Kept OUT of shifts.json so the shift record stays minimal.
* admin.json     : AdminData (password hash + payroll Settings)
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# --- Core records ------------------------------------------------------------

class Role(BaseModel):
    """
    A job an employee can work, e.g. "Cashier" in the "Bowling" department at
    $17.60/hr. One employee can hold several roles, possibly in different
    departments, each with its own rate. Chosen at clock-in (see Shift).
    """
    id: str
    title: str                    # e.g. "Cashier", "Bartender", "Maintenance"
    department: str               # e.g. "Bowling", "Restaurant", "Maintenance"
    hourly_rate: float = 0.0      # dollars/hour


class Employee(BaseModel):
    id: str                       # stable opaque id (uuid4 hex, short)
    first_name: str                # stored ONLY in employees.json
    last_name: str                 # stored ONLY in employees.json
    # Optional name the employee goes by; when set it replaces the first name
    # on the kiosk (see short_name). The legal first/last are still kept for
    # records/payroll.
    preferred_name: str = ""
    active: bool = True           # inactive employees hide from the kiosk
    roles: list[Role] = Field(default_factory=list)
    # Vacation pay accrual rate, percent of gross wages. Ontario ESA minimum is
    # 4% (6% once the employee reaches 5 years of service — bump it manually).
    # Shown as its own column in the payroll export ("paid as earned" style).
    vacation_pay_percent: float = 4.0

    @property
    def name(self) -> str:
        """Full display name, e.g. for payroll rows, the admin panel, audit."""
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def short_name(self) -> str:
        """First name + last initial for the public kiosk, dropping any middle
        names/initials (which live in first_name, e.g. "Emma M" -> "Emma B.").
        A preferred_name, when set, replaces the derived first name
        ("Kulbir" who goes by "Kevin" -> "Kevin J."). Falls back gracefully
        when a name part is missing."""
        given = self.preferred_name.strip() or (self.first_name.strip().split() or [""])[0]
        last = self.last_name.strip()
        if given and last:
            return f"{given} {last[0].upper()}."
        return given or last


class Shift(BaseModel):
    id: str
    employee_id: str
    clock_in: str                 # ISO 8601 local timestamp string
    clock_out: Optional[str] = None  # None => still open (not clocked out)
    # Snapshot of the role worked, taken at clock-in (None for shifts recorded
    # before roles existed). Preserved even if the role is later renamed,
    # moved to another department, deleted, or re-rated.
    role_id: Optional[str] = None
    role_title: Optional[str] = None
    department: Optional[str] = None
    hourly_rate: Optional[float] = None
    # Raw duration in hours (clock_out - clock_in), NOT break-adjusted —
    # matches the admin Shifts page's "Hours" column. Filled in on clock-out
    # and recomputed on any admin edit; None while the shift is still open.
    hours: Optional[float] = None
    # True if this shift was closed by the automatic-clock-out safety net
    # (see repo.auto_close_stale_shifts) rather than a real clock-out — the
    # recorded clock_out is when the app happened to notice, not necessarily
    # when the employee actually left. Flagged in the admin Shifts page.
    auto_clocked_out: bool = False
    # Soft delete: a voided shift is KEPT on disk for the historical record but
    # treated as if removed everywhere operational — excluded from payroll and
    # the Shifts-page summary, ignored by clock-state/auto-clockout logic. An
    # admin can un-void it. Preferred over hard deletion so nothing is lost.
    voided: bool = False


# --- Payroll settings (Ontario) ----------------------------------------------

class BreakSettings(BaseModel):
    enabled: bool = True
    # Unpaid meal break length to deduct, in minutes.
    duration_minutes: int = 30
    # Only deduct when a shift exceeds this many consecutive hours. Ontario ESA
    # entitles a 30-min eating period after no more than 5 consecutive hours.
    trigger_hours: float = 5.0


class OvertimeSettings(BaseModel):
    enabled: bool = True
    multiplier: float = 1.5       # 1.5x regular rate
    weekly_threshold: float = 44.0  # ESA: hours over 44 in a work week
    # Work-week start day, Python weekday convention Monday=0 .. Sunday=6.
    # This is the SINGLE source of truth for "week" everywhere in the app: it
    # drives overtime bucketing, the /data shift storage layout, and the admin
    # Shifts page's week navigation. Default Sunday, matching how this business
    # schedules its work week.
    week_start_weekday: int = 6


class MinWageSettings(BaseModel):
    # Ontario general minimum wage. 17.60 as of 2026-07-04; rises to 17.95 on
    # 2026-10-01. Editable so the operator keeps it current.
    rate: float = 17.60


class AutoClockoutSettings(BaseModel):
    enabled: bool = True
    # Safety net for a forgotten clock-out: a shift still open past this many
    # hours gets closed automatically. There is no background timer — this is
    # checked opportunistically whenever the kiosk or admin panel is next
    # loaded (see repo.auto_close_stale_shifts), so the recorded clock-out is
    # whenever the app happened to notice, not exactly the threshold.
    threshold_hours: float = 24.0


class ClockSafetySettings(BaseModel):
    """Grace window for correcting kiosk mis-taps. Within ``buffer_minutes`` of
    the relevant event:
      * clocking an employee back IN re-opens the shift they just clocked out
        of (same role) instead of starting a new one — undoes an accidental
        clock-out and continues the original shift.
      * clocking OUT right after clocking in removes the just-opened shift
        entirely — undoes an accidental clock-in so no stray shift is recorded.
    """
    enabled: bool = True
    buffer_minutes: int = 15


class Settings(BaseModel):
    break_rules: BreakSettings = Field(default_factory=BreakSettings)
    overtime: OvertimeSettings = Field(default_factory=OvertimeSettings)
    min_wage: MinWageSettings = Field(default_factory=MinWageSettings)
    auto_clockout: AutoClockoutSettings = Field(default_factory=AutoClockoutSettings)
    clock_safety: ClockSafetySettings = Field(default_factory=ClockSafetySettings)
    # Master list of departments -> valid role titles within them, maintained
    # in Admin > Settings. Populates the department/title dropdowns when
    # assigning roles to an employee (app/templates/admin_employees.html), so
    # naming stays consistent instead of free text (which would silently
    # create a new/duplicate kiosk department section on any typo).
    role_catalog: dict[str, list[str]] = Field(
        default_factory=lambda: {"General": ["General"]}
    )


class AdminData(BaseModel):
    # bcrypt hash of the admin password. None => not configured yet (first run).
    password_hash: Optional[str] = None
    settings: Settings = Field(default_factory=Settings)


# --- Per-shift break override -------------------------------------------------

class BreakOverride(BaseModel):
    """
    Optional manual override for a single shift's unpaid break, stored in the
    week's adjustments.json keyed by shift id. ``minutes`` is the unpaid break to
    deduct for that shift regardless of the automatic trigger rule (0 = the
    employee did not take a break, so deduct nothing).
    """
    minutes: int = 0
