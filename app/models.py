"""
Pydantic data models and default settings.

Storage schemas are deliberately minimal and human-readable:

* employees.json : list[Employee]  -> names live ONLY here
* shifts.json    : list[Shift]      -> {id, employee_id, clock_in, clock_out} only
* adjustments.json (per week, optional) : per-shift break overrides, keyed by
  shift id. Kept OUT of shifts.json so the shift record stays exactly the four
  required fields and never carries a name.
* admin.json     : AdminData (password hash + payroll Settings)
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# --- Core records ------------------------------------------------------------

class Employee(BaseModel):
    id: str                       # stable opaque id (uuid4 hex, short)
    name: str                     # display name — stored ONLY in employees.json
    hourly_rate: float = 0.0      # dollars/hour
    active: bool = True           # inactive employees hide from the kiosk


class Shift(BaseModel):
    id: str
    employee_id: str
    clock_in: str                 # ISO 8601 local timestamp string
    clock_out: Optional[str] = None  # None => still open (not clocked out)


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
    week_start_weekday: int = 0


class MinWageSettings(BaseModel):
    # Ontario general minimum wage. 17.60 as of 2026-07-04; rises to 17.95 on
    # 2026-10-01. Editable so the operator keeps it current.
    rate: float = 17.60


class Settings(BaseModel):
    break_rules: BreakSettings = Field(default_factory=BreakSettings)
    overtime: OvertimeSettings = Field(default_factory=OvertimeSettings)
    min_wage: MinWageSettings = Field(default_factory=MinWageSettings)


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
