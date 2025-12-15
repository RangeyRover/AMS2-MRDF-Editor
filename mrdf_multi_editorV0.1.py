# mrdf_multi_editor.py
# AMS2/ Project CARS 2 - Multi-profile MRDF fixed-offset editor + Hex Viewer/Editor
# Supports multiple MRDF definition profiles (e.g. Stats + Physics Tweaker)
# In-place edits only (payload size must not change).

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

Scalar = Literal["float", "int32", "uint32", "bool32", "uint8"]

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


# -----------------------------
# Data model
# -----------------------------
@dataclass(frozen=True)
class MrdfFieldDef:
    name: str
    section: str
    offset: int
    scalar: Scalar
    notes: str = ""
    enum: Optional[Dict[int, str]] = None


@dataclass
class MrdfFieldInstance:
    definition: MrdfFieldDef
    offset: int
    raw: bytes
    value: Any


# -----------------------------
# Binary helpers
# -----------------------------
_FMT: Dict[Scalar, Tuple[str, int]] = {
    "float":  ("<f", 4),
    "int32":  ("<i", 4),
    "uint32": ("<I", 4),
    "bool32": ("<I", 4),   # stored as 0/1 in a u32 slot
    "uint8":  ("<B", 1),   # single byte
}

def read_scalar(blob: bytes, off: int, scalar: Scalar) -> Tuple[Any, bytes]:
    fmt, n = _FMT[scalar]
    if off < 0 or off + n > len(blob):
        raise ValueError(f"Out of bounds read at {off:#x} ({scalar})")
    raw = blob[off:off+n]
    v = struct.unpack_from(fmt, blob, off)[0]
    if scalar == "bool32":
        v = 1 if int(v) != 0 else 0
    return v, raw

def write_scalar(buf: bytearray, off: int, scalar: Scalar, v: Any) -> bytes:
    fmt, n = _FMT[scalar]
    if off < 0 or off + n > len(buf):
        raise ValueError(f"Out of bounds write at {off:#x} ({scalar})")

    if scalar == "float":
        packed = struct.pack(fmt, float(v))
    elif scalar == "uint8":
        packed = struct.pack(fmt, int(v) & 0xFF)
    else:
        iv = int(v)
        if scalar == "bool32":
            iv = 1 if iv else 0
        packed = struct.pack(fmt, iv & 0xFFFFFFFF)

    buf[off:off+n] = packed
    return packed

def is_printable(b: int) -> bool:
    return 32 <= b <= 126

def format_hex_lines(blob: bytes, start: int, nbytes: int, bytes_per_line: int = 16) -> List[str]:
    """
    Standard hex dump:
    OFFSET  HEX...  |ASCII...|
    Note: MRDF data is mostly floats/ints, so ASCII will usually be dots. That's normal.
    """
    end = min(len(blob), start + nbytes)
    lines: List[str] = []
    for off in range(start, end, bytes_per_line):
        chunk = blob[off:off+bytes_per_line]
        hex_part = " ".join(f"{x:02X}" for x in chunk)
        hex_part = hex_part.ljust(bytes_per_line * 3 - 1)
        ascii_part = "".join(chr(x) if is_printable(x) else "." for x in chunk)
        lines.append(f"{off:08X}  {hex_part}  |{ascii_part}|")
    return lines

def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def parse_mrdf(blob: bytes, defs: List[MrdfFieldDef]) -> List[MrdfFieldInstance]:
    insts: List[MrdfFieldInstance] = []
    for d in defs:
        try:
            v, raw = read_scalar(blob, d.offset, d.scalar)
        except Exception:
            continue
        insts.append(MrdfFieldInstance(definition=d, offset=d.offset, raw=raw, value=v))
    insts.sort(key=lambda i: (i.definition.section, i.offset))
    return insts


# -----------------------------
# MRDF Definitions: STATS
# -----------------------------
ENGINE_TYPE = {
    0x00: "Don't use",
    0x01: "V6",
    0x02: "V8",
    0x03: "V10",
    0x04: "V12",
    0x05: "Straight 4",
    0x06: "Straight 5",
    0x07: "Straight 6",
    0x08: "Rotary 2",
    0x09: "Rotary 3",
    0x0A: "Flat 4",
    0x0B: "Flat 6",
    0x0C: "W16",
    0x0D: "W12",
    0x0E: "Single Cylinder",
    0x0F: "Twin Cylinder",
    0x10: "Flat 8",
    0x11: "Flat 12",
}

DRIVETRAIN = {0: "RWD", 1: "AWD", 2: "FWD"}
BOOST_TYPE = {0: "Natural Aspiration", 1: "Supercharged", 2: "Turbo"}
ASPIRATION = {0: "Naturally aspirated", 1: "Boosted"}
SHIFT_TYPE = {0: "H Pattern", 1: "Sequential"}

# Tyre availability bitmask at 0xBC - single byte
# Bits 0-7 control individual compounds
TYRE_BITS = [
    (0x01, "Soft / Semi Slick"),
    (0x02, "Medium"),
    (0x04, "Hard"),
    (0x08, "Intermediate"),
    (0x10, "Wet"),
    (0x20, "Extreme"),
    (0x40, "All Weather"),
]


STATS_MRDF_DEFS: List[MrdfFieldDef] = [
    MrdfFieldDef("TopSpeed_mps",             "PERFORMANCE", 0x20, "float",  "Top speed in meters/sec. MPH*0.447"),
    MrdfFieldDef("Accel_0_100_kmh",          "PERFORMANCE", 0x24, "float",  "0-100km/h time (seconds)"),
    MrdfFieldDef("Gear_for_100_kmh",         "PERFORMANCE", 0x28, "uint32", "Gear needed to reach 100km/h"),
    MrdfFieldDef("Accel_0_160_kmh",          "PERFORMANCE", 0x2C, "float",  "0-160km/h time (seconds)"),
    MrdfFieldDef("Gear_for_160_kmh",         "PERFORMANCE", 0x30, "uint32", "Gear needed to reach 160km/h"),
    MrdfFieldDef("Braking_100_0_kmh",        "PERFORMANCE", 0x34, "float",  "100-0km/h time (seconds)"),
    MrdfFieldDef("PerformanceIndex_PI",      "PERFORMANCE", 0x38, "float",  "Performance rating (not used in-game)"),
    MrdfFieldDef("Mass_kg",                  "PERFORMANCE", 0x3C, "float",  "Mass in kg"),

    MrdfFieldDef("NumGears",                 "DRIVETRAIN",  0x40, "uint32", "Number of gears in transmission"),
    MrdfFieldDef("Torque_lbft",              "DRIVETRAIN",  0x44, "float",  "Torque in lb-ft"),
    MrdfFieldDef("HP_SAE_Net",               "DRIVETRAIN",  0x48, "float",  "Horsepower (SAE Net)"),
    MrdfFieldDef("DrivetrainType",           "DRIVETRAIN",  0x4C, "uint32", "00=RWD,01=AWD,02=FWD", enum=DRIVETRAIN),

    MrdfFieldDef("BoostType",                "ENGINE",      0x50, "uint32", "0=NA,1=Supercharged,2=Turbo", enum=BOOST_TYPE),
    MrdfFieldDef("Aspiration",               "ENGINE",      0x54, "uint32", "0=NA,1=Boosted", enum=ASPIRATION),
    MrdfFieldDef("HandlingPerformance",      "HANDLING",    0x58, "float",  "Handling performance"),
    MrdfFieldDef("Unknown_0x5C",             "UNKNOWN",     0x5C, "uint32", "Unknown"),
    MrdfFieldDef("Unknown_0x60",             "UNKNOWN",     0x60, "uint32", "Unknown"),

    MrdfFieldDef("EngineType",               "ENGINE",      0x64, "uint32", "Engine type enum", enum=ENGINE_TYPE),
    MrdfFieldDef("TierLevel",                "META",        0x68, "uint32", "Tier level"),

    MrdfFieldDef("Unknown_0x6C",             "UNKNOWN",     0x6C, "float",  "Unknown float"),
    MrdfFieldDef("Unknown_0x70",             "UNKNOWN",     0x70, "float",  "Unknown float"),
    MrdfFieldDef("Unknown_0x74",             "UNKNOWN",     0x74, "uint32", "Unknown (often 0x80000000 in sample)"),
    MrdfFieldDef("Unknown_0x78",             "UNKNOWN",     0x78, "float",  "Unknown float"),
    MrdfFieldDef("Unknown_0x7C",             "UNKNOWN",     0x7C, "float",  "Unknown float"),

    MrdfFieldDef("BodyHeightAdjust_m",       "CHASSIS",     0x80, "float",  "Menu-only body height adjust (m)"),
    MrdfFieldDef("Wheelbase_m",              "CHASSIS",     0x84, "float",  "Wheelbase (m)"),
    MrdfFieldDef("RearWeightDistribution",   "CHASSIS",     0x88, "float",  "Rear weight distribution (0..1)"),

    MrdfFieldDef("ABS",                      "ASSISTS",     0x8C, "bool32", "ABS enabled (1=true)"),
    MrdfFieldDef("TC",                       "ASSISTS",     0x90, "bool32", "Traction Control (1=true)"),
    MrdfFieldDef("SC",                       "ASSISTS",     0x94, "bool32", "Stability Control (1=true)"),

    MrdfFieldDef("CorneringDifficulty",      "HANDLING",    0x98, "uint32", "Cornering difficulty (1,2,3)"),
    MrdfFieldDef("CorneringSpeed",           "HANDLING",    0x9C, "uint32", "Cornering speed (1,2,3)"),

    # Candidate list says 0xA0 is Engine Displacement
    MrdfFieldDef("EngineDisplacement",       "ENGINE",      0xA0, "float", "Engine displacement (units: litres)"),

    MrdfFieldDef("ShiftType",                "DRIVETRAIN",  0xA4, "uint32", "00=H Pattern,01=Sequential", enum=SHIFT_TYPE),

    MrdfFieldDef("DRS_Enabled",              "AERO",        0xA8, "bool32", "DRS available (1=true)"),

    # 0xAC-0xAF Boost Button (push-to-pass / boost)
    MrdfFieldDef("BoostButton",              "ENGINE",      0xAC, "bool32", "Boost button available (1=true)"),

    MrdfFieldDef("AdjustableTurbo",          "ENGINE",      0xB0, "bool32", "Adjustable turbo available (1=true)"),

    # 0xB4-0xB7 Onboard Roll Bars adjustment available
    MrdfFieldDef("OnboardRollBars",          "CHASSIS",     0xB4, "bool32", "Onboard roll bars adjustable (1=true)"),

    # 0xB8-0xBB Onboard Brake Bias adjustment available
    MrdfFieldDef("OnboardBrakeBias",         "BRAKES",      0xB8, "bool32", "Onboard brake bias adjustable (1=true)"),

    # Tyre availability bitmask at 0xBC (candidate shows uint32: 0x00000011 etc.)
    MrdfFieldDef("TyreAvailability",         "TYRES",       0xBC, "uint32",
                 "Tyre availability bitmask in low byte (0x40=All Weather override; else bits 0-5: Soft/Med/Hard/Inter/Wet/Unknown)"),

    # 0xC0-0xC3 Headlights available
    MrdfFieldDef("Headlights",               "ELECTRICAL",  0xC0, "bool32", "Headlights available (1=true)"),

    # 0xC4-0xC7 Pit limiter available
    MrdfFieldDef("PitLimiter",               "DRIVETRAIN",  0xC4, "bool32", "Pit limiter available (1=true)"),
]


# -----------------------------
# MRDF Definitions: PHYSICS TWEAKER
# -----------------------------
TICK_RATE = {180: "180 Hz", 360: "360 Hz", 540: "540 Hz"}
BOOL01 = {0: "False", 1: "True"}

PHYSICS_MRDF_DEFS: List[MrdfFieldDef] = [
    MrdfFieldDef("BrakeGlowMinTemp",     "BRAKES",   0x0030, "float",  "Brake glow minimum temp (float)"),
    MrdfFieldDef("BrakeGlowMaxTemp",     "BRAKES",   0x0034, "float",  "Brake glow maximum temp (float)"),
    MrdfFieldDef("BrakeGlowScaleAI",     "BRAKES",   0x0038, "float",  "Brake glow scale for AI (float)"),
    MrdfFieldDef("BrakeGlowScalePlayer", "BRAKES",   0x003C, "float",  "Brake glow scale for Player (float)"),

    MrdfFieldDef("ContinuousCDThickness","JOINTS",   0x0048, "float",  "Continuous CD Thickness (float)"),
    # Your pasted definition says "float", but if the file truly contains 06 00 00 00 then it is an int.
    # You can flip this to uint32 if you confirm it's integer in the actual file.
    MrdfFieldDef("JointIterations",      "JOINTS",   0x004C, "uint32", "Joint Iterations (often looks integer in dumps)"),
    MrdfFieldDef("JointStrength",        "JOINTS",   0x0050, "float",  "Joint Strength (float)"),

    MrdfFieldDef("EnableAntiFlipAid",    "ANTI-FLIP",0x0054, "uint32", "Enable Anti Flip Aid (0/1)", enum=BOOL01),
    MrdfFieldDef("AntiFlipMinAngle",     "ANTI-FLIP",0x0058, "float",  "Anti Flip Minimum Angle (deg)"),
    MrdfFieldDef("AntiFlipMaxAngle",     "ANTI-FLIP",0x005C, "float",  "Anti Flip Maximum Angle (deg)"),
    MrdfFieldDef("AntiFlipTorqueForce",  "ANTI-FLIP",0x0060, "float",  "Anti Flip Torque Fixing Force"),
    MrdfFieldDef("AntiFlipOrientForce",  "ANTI-FLIP",0x0064, "float",  "Anti Flip Orientation Fixing Force"),

    MrdfFieldDef("MinBumpStopForce",     "SUSPENSION",0x02F0,"float",  "Minimum Bump Stop Force"),
    MrdfFieldDef("MaxBumpStopForce",     "SUSPENSION",0x02F4,"float",  "Maximum Bump Stop Force"),

    MrdfFieldDef("DraftMinSpeed",        "DRAFTING", 0x02F8, "float",  "Drafting Minimum Speed"),
    MrdfFieldDef("DraftRampSpeed",       "DRAFTING", 0x02FC, "float",  "Drafting Ramp Speed"),
    MrdfFieldDef("DraftMaxSpeed",        "DRAFTING", 0x0300, "float",  "Drafting Maximum Speed"),
    MrdfFieldDef("DraftMaxDistFront",    "DRAFTING", 0x0304, "float",  "Drafting Max Distance In Front"),
    MrdfFieldDef("DraftMinLatFront",     "DRAFTING", 0x0308, "float",  "Drafting Min Lateral In Front"),
    MrdfFieldDef("DraftMaxLatFront",     "DRAFTING", 0x030C, "float",  "Drafting Max Lateral In Front"),
    MrdfFieldDef("DraftMaxDistBehind",   "DRAFTING", 0x0310, "float",  "Drafting Max Distance Behind"),
    MrdfFieldDef("DraftMinLatBehind",    "DRAFTING", 0x0314, "float",  "Drafting Min Lateral Behind"),
    MrdfFieldDef("DraftMaxLatBehind",    "DRAFTING", 0x0318, "float",  "Drafting Max Lateral Behind"),
    MrdfFieldDef("DraftAirScale",        "DRAFTING", 0x031C, "float",  "Drafting Air Scale"),

    MrdfFieldDef("LowSpeedTCAtRest",     "ASSISTS",  0x0320, "float",  "Low Speed TC At Rest"),
    MrdfFieldDef("LowSpeedTCSpeedThresh","ASSISTS",  0x0324, "float",  "Low Speed TC Speed Threshold"),
    MrdfFieldDef("LowSpeedGripAtRest",   "ASSISTS",  0x0328, "float",  "Low Speed Grip At Rest"),
    MrdfFieldDef("LowSpeedGripSpeedTh",  "ASSISTS",  0x032C, "float",  "Low Speed Grip Speed Threshold"),

    MrdfFieldDef("AutoResetDisableCollT","RESET",    0x0330, "float",  "Auto Reset - Time Collision Is Disabled (s)"),
    MrdfFieldDef("AutoResetMinSpeedMPH", "RESET",    0x0334, "float",  "Auto Reset - Minimum Speed in MPH"),

    MrdfFieldDef("AutoClutch",           "ASSISTS",  0x0378, "uint32", "Auto Clutch (0/1)"),
    MrdfFieldDef("SteeringHelpFunction", "ASSISTS",  0x037C, "uint32", "Steering help function (int)"),

    MrdfFieldDef("PhysicsTickRate",      "PHYSICS",  0x0380, "uint32", "Physics tick rate", enum=TICK_RATE),
    MrdfFieldDef("AutoReverse",          "ASSISTS",  0x0384, "uint32", "Auto reverse (0/1)", enum=BOOL01),
    MrdfFieldDef("AutoShiftOverrideTime","ASSISTS",  0x0388, "float",  "Auto shift override time (s)"),
    MrdfFieldDef("ManShiftOverrideTime", "ASSISTS",  0x038C, "float",  "Manual shift override time (s)"),

    MrdfFieldDef("AIStrengthNovice",     "AI",       0x03B0, "float",  "AI Strength Novice"),
    MrdfFieldDef("AIStrengthAmateur",    "AI",       0x03B4, "float",  "AI Strength Amateur"),
    MrdfFieldDef("AIStrengthPro",        "AI",       0x03B8, "float",  "AI Strength Pro"),

    MrdfFieldDef("AIAggressionNovice",   "AI",       0x03BC, "float",  "AI Aggression Novice"),
    MrdfFieldDef("AIAggressionNormal",   "AI",       0x03C0, "float",  "AI Aggression Normal"),
    MrdfFieldDef("AIAggressionXP",       "AI",       0x03C4, "float",  "AI Aggression XP"),
    MrdfFieldDef("AIAggressionPro",      "AI",       0x03C8, "float",  "AI Aggression Pro"),

    MrdfFieldDef("FuelMult",             "AI",       0x03D4, "uint32", "Fuel mult (int)"),
    MrdfFieldDef("TyreMult",             "AI",       0x03D8, "uint32", "Tire mult (int)"),
]


# -----------------------------
# Profile system
# -----------------------------
@dataclass(frozen=True)
class MrdfProfile:
    key: str
    label: str
    defs: List[MrdfFieldDef]

PROFILES: List[MrdfProfile] = [
    MrdfProfile("stats",   "Statistics MRDF", STATS_MRDF_DEFS),
    MrdfProfile("physics", "Physics Tweaker MRDF", PHYSICS_MRDF_DEFS),
]

def get_profile_by_key(key: str) -> MrdfProfile:
    for p in PROFILES:
        if p.key == key:
            return p
    return PROFILES[0]

def detect_profile(file_path: str, blob: bytes) -> MrdfProfile:
    """
    Best-effort detection:
    - If path hints, use that
    - Else sanity-check known offsets for plausible floats
    """
    lower = (file_path or "").lower()

    if "physicstweaker" in lower or os.sep + "physics" + os.sep in lower:
        return get_profile_by_key("physics")

    # Heuristic: physics has floats at 0x0030..0x003C around 600/1200/0.8/1.0 (commonly).
    try:
        bmin = struct.unpack_from("<f", blob, 0x0030)[0]
        bmax = struct.unpack_from("<f", blob, 0x0034)[0]
        ai   = struct.unpack_from("<f", blob, 0x0038)[0]
        pl   = struct.unpack_from("<f", blob, 0x003C)[0]
        if 100.0 < bmin < 5000.0 and 100.0 < bmax < 10000.0 and 0.0 <= ai <= 10.0 and 0.0 <= pl <= 10.0:
            return get_profile_by_key("physics")
    except Exception:
        pass

    # Heuristic: stats has Wheelbase at 0x84 typically 1.5..4.5 (meters)
    try:
        wb = struct.unpack_from("<f", blob, 0x84)[0]
        if 1.0 < wb < 6.0:
            return get_profile_by_key("stats")
    except Exception:
        pass

    # Default
    return get_profile_by_key("stats")


# -----------------------------
# UI
# -----------------------------
class MrdfEditorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MRDF Editor (AMS2)")
        self.geometry("1450x860")

        self.file_path: Optional[str] = None
        self.original_blob: Optional[bytes] = None
        self.working_blob: Optional[bytes] = None
        self.instances: List[MrdfFieldInstance] = []

        # active profile
        self.profile: MrdfProfile = PROFILES[0]

        # selection
        self._selected: Optional[MrdfFieldInstance] = None
        self._editor_var = tk.StringVar(value="")
        self._enum_choice_var = tk.StringVar(value="")

        # edits bookkeeping
        self.edits: Dict[int, Any] = {}  # offset -> new_value

        # bitmask editor checkboxes (for TyreAvailability)
        self._bitmask_vars: Dict[int, tk.BooleanVar] = {}
        self._bitmask_bittext: Dict[int, tk.StringVar] = {}
        self._tyre_bits_binvar = tk.StringVar(value="00000000")

        # hex view
        self.hex_bytes_per_page = 16 * 64
        self.hex_anchor = 0
        self._hex_line_index: Dict[int, int] = {}
        self._iid_by_offset: Dict[int, str] = {}

        self._build_menu()
        self._build_layout()

    def _build_menu(self):
        m = tk.Menu(self)

        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="Open…", command=self.open_file)
        fm.add_command(label="Save", command=self.save_file, state="disabled")
        fm.add_command(label="Save As…", command=self.save_file_as, state="disabled")
        fm.add_separator()
        fm.add_command(label="Exit", command=self.destroy)
        m.add_cascade(label="File", menu=fm)
        self._file_menu = fm

        tm = tk.Menu(m, tearoff=0)
        tm.add_command(label="Re-parse (refresh view)", command=self.refresh_parse, state="disabled")
        tm.add_command(label="Discard unsaved edits", command=self.discard_edits, state="disabled")
        m.add_cascade(label="Tools", menu=tm)
        self._tools_menu = tm

        self.config(menu=m)

    def _update_tyre_bit_displays(self):
        """Refresh the per-bit 0/1 labels + the combined 8-bit string."""
        # Update individual 0/1 indicators (if present)
        for mask, var in self._bitmask_vars.items():
            if hasattr(self, "_bitmask_bittext") and mask in self._bitmask_bittext:
                self._bitmask_bittext[mask].set("1" if var.get() else "0")

        # Build full 8-bit string (bit7..bit0). We only expose masks up to 0x40, so bit7 will usually be 0.
        byte_val = 0
        for mask, var in self._bitmask_vars.items():
            if var.get():
                byte_val |= mask

        self._tyre_bits_binvar.set(f"{byte_val & 0xFF:08b}")


    def _build_layout(self):
        v = ttk.Panedwindow(self, orient="vertical")
        v.pack(fill="both", expand=True)

        outer = ttk.Panedwindow(v, orient="horizontal")
        v.add(outer, weight=3)

        left = ttk.Frame(outer, padding=8)
        outer.add(left, weight=3)

        right = ttk.Frame(outer, padding=8)
        outer.add(right, weight=2)

        hexpane = ttk.Frame(v, padding=8)
        v.add(hexpane, weight=2)

        # ---------------- left panel ----------------
        topbar = ttk.Frame(left)
        topbar.pack(fill="x", pady=(0, 6))

        style = ttk.Style(self)
        style.configure("Treeview", rowheight=28)

        ttk.Label(topbar, text="Profile:").pack(side="left")
        self.profile_var = tk.StringVar(value=self.profile.label)
        self.profile_combo = ttk.Combobox(
            topbar,
            textvariable=self.profile_var,
            values=[p.label for p in PROFILES],
            state="readonly",
            width=26
        )
        self.profile_combo.pack(side="left", padx=(6, 14))
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_changed)

        ttk.Label(topbar, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._rebuild_tree())
        ttk.Entry(topbar, textvariable=self.filter_var, width=40).pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="Open a MRDF file to begin.")
        ttk.Label(left, textvariable=self.status_var).pack(fill="x", pady=(0, 6))

        self.tree = ttk.Treeview(left, columns=("value", "type", "offset"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Field")
        self.tree.heading("value", text="Value")
        self.tree.heading("type", text="Type")
        self.tree.heading("offset", text="Offset (hex)")
        self.tree.column("#0", width=360)
        self.tree.column("value", width=320)
        self.tree.column("type", width=120)
        self.tree.column("offset", width=120, anchor="e")

        ysb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # ---------------- right panel ----------------
        ttk.Label(right, text="Selected field", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        self.sel_title = tk.StringVar(value="(none)")
        ttk.Label(right, textvariable=self.sel_title, wraplength=520).pack(anchor="w", pady=(4, 8))

        self.meta_text = tk.Text(right, height=10, width=60, wrap="word")
        self.meta_text.configure(state="disabled")
        self.meta_text.pack(fill="x", pady=(0, 10))

        ttk.Label(right, text="Edit value", font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.editor_frame = ttk.Frame(right)
        self.editor_frame.pack(fill="x", pady=(6, 10))

        self.value_entry = ttk.Entry(self.editor_frame, textvariable=self._editor_var, width=30)
        self.value_entry.grid(row=0, column=0, sticky="w")

        self.enum_combo = ttk.Combobox(self.editor_frame, textvariable=self._enum_choice_var, width=44, state="readonly")
        self.enum_combo.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.enum_combo.grid_remove()

        # Bitmask editor frame (for TyreAvailability)
        self.bitmask_frame = ttk.LabelFrame(self.editor_frame, text="Tyre Availability Bits")
        self.bitmask_frame.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.bitmask_frame.grid_remove()
        
        # 8-bit view label (e.g. 00110001)
        ttk.Label(self.bitmask_frame, text="Bits:").grid(row=0, column=0, sticky="w", padx=4, pady=(2, 6))
        ttk.Label(self.bitmask_frame, textvariable=self._tyre_bits_binvar).grid(row=0, column=1, sticky="e", padx=(10, 4), pady=(2, 6))

        # Create checkbox for each bit + adjacent 0/1 display
        for i, (mask, label) in enumerate(TYRE_BITS):
            var = tk.BooleanVar(value=False)
            self._bitmask_vars[mask] = var

            bit_txt = tk.StringVar(value="0")
            self._bitmask_bittext[mask] = bit_txt

            cb = ttk.Checkbutton(self.bitmask_frame, text=f"0x{mask:02X} - {label}", variable=var)
            cb.grid(row=i+1, column=0, sticky="w", padx=4, pady=1)   # note i+1

            ttk.Label(self.bitmask_frame, textvariable=bit_txt, width=2, anchor="e").grid(
                row=i+1, column=1, sticky="e", padx=(10, 4), pady=1  # note i+1
            )

            # keep indicator + 8-bit string synced when checkbox changes
            var.trace_add("write", lambda *_args: self._update_tyre_bit_displays())

        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(8, 0))
        self.apply_btn = ttk.Button(btns, text="Apply Edit", command=self.apply_edit, state="disabled")
        self.apply_btn.pack(side="left")
        self.revert_btn = ttk.Button(btns, text="Revert Field", command=self.revert_field, state="disabled")
        self.revert_btn.pack(side="left", padx=8)

        # ---------------- bottom hex pane ----------------
        hex_top = ttk.Frame(hexpane)
        hex_top.pack(fill="x", pady=(0, 6))

        ttk.Label(hex_top, text="Hex view", font=("Segoe UI", 11, "bold")).pack(side="left")

        ttk.Label(hex_top, text="Jump to offset (hex):").pack(side="left", padx=(16, 4))
        self.jump_var = tk.StringVar(value="0")
        ttk.Entry(hex_top, textvariable=self.jump_var, width=12).pack(side="left")
        ttk.Button(hex_top, text="Go", command=self.hex_jump).pack(side="left", padx=6)

        ttk.Button(hex_top, text="◀ Prev", command=lambda: self.hex_page(-1)).pack(side="left", padx=(16, 4))
        ttk.Button(hex_top, text="Next ▶", command=lambda: self.hex_page(+1)).pack(side="left")

        self.hex_info_var = tk.StringVar(value="")
        ttk.Label(hex_top, textvariable=self.hex_info_var).pack(side="right")

        hex_mid = ttk.Frame(hexpane)
        hex_mid.pack(fill="both", expand=True)

        self.hex_text = tk.Text(hex_mid, height=1, wrap="none", font=("Consolas", 10))
        self.hex_text.tag_configure("sel_value", background="#D9EAD3")
        self.hex_text.configure(state="disabled")

        xsb = ttk.Scrollbar(hex_mid, orient="horizontal", command=self.hex_text.xview)
        ysb2 = ttk.Scrollbar(hex_mid, orient="vertical", command=self.hex_text.yview)
        self.hex_text.configure(xscroll=xsb.set, yscroll=ysb2.set)

        self.hex_text.pack(side="left", fill="both", expand=True)
        ysb2.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")

        # overwrite
        hex_edit = ttk.LabelFrame(hexpane, text="Hex overwrite (in-place)")
        hex_edit.pack(fill="x", pady=(8, 0))

        row = ttk.Frame(hex_edit)
        row.pack(fill="x", padx=8, pady=6)

        # allow the entry column to shrink/grow
        row.columnconfigure(3, weight=1)

        ttk.Label(row, text="Target:").grid(row=0, column=0, sticky="w")
        self.hex_target_var = tk.StringVar(value="(none)")
        ttk.Label(row, textvariable=self.hex_target_var).grid(row=0, column=1, sticky="w", padx=(6, 16))

        ttk.Label(row, text="Bytes (space-separated hex):").grid(row=0, column=2, sticky="w")

        self.hex_edit_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.hex_edit_var).grid(row=0, column=3, sticky="ew", padx=6)

        self.hex_apply_btn = ttk.Button(row, text="Overwrite", command=self.apply_hex_overwrite, state="disabled"        )
        self.hex_apply_btn.grid(row=0, column=4, sticky="w", padx=6)

        self.hex_revert_btn = ttk.Button(row, text="Revert bytes", command=self.revert_hex_overwrite, state="disabled"        )
        self.hex_revert_btn.grid(row=0, column=5, sticky="w", padx=6)

        self._hex_sel_start: Optional[int] = None
        self._hex_sel_len: Optional[int] = None

    # -----------------------------
    # Profile switching
    # -----------------------------
    def on_profile_changed(self, _evt=None):
        label = self.profile_var.get()
        for p in PROFILES:
            if p.label == label:
                self.profile = p
                break
        # re-parse with new defs if a file is loaded
        if self.working_blob is not None:
            self.refresh_parse()

    def _set_profile(self, p: MrdfProfile):
        self.profile = p
        self.profile_var.set(p.label)
        if self.working_blob is not None:
            self.refresh_parse()

    # -----------------------------
    # File actions
    # -----------------------------
    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open MRDF file",
            filetypes=[("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            return

        self.file_path = path
        self.original_blob = blob
        self.working_blob = blob
        self.edits.clear()

        # auto-detect profile
        detected = detect_profile(path, blob)
        self._set_profile(detected)

        self._file_menu.entryconfig("Save", state="normal")
        self._file_menu.entryconfig("Save As…", state="normal")
        self._tools_menu.entryconfig("Re-parse (refresh view)", state="normal")
        self._tools_menu.entryconfig("Discard unsaved edits", state="normal")

        self.hex_anchor = 0
        self._refresh_hex_view()

    def save_file(self):
        if not self.file_path or self.working_blob is None:
            return
        try:
            with open(self.file_path, "wb") as f:
                f.write(self.working_blob)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        messagebox.showinfo("Saved", "File saved successfully.")

    def save_file_as(self):
        if self.working_blob is None:
            return
        path = filedialog.asksaveasfilename(
            title="Save As",
            defaultextension=".mrdf",
            filetypes=[("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self.working_blob)
        except Exception as e:
            messagebox.showerror("Save As failed", str(e))
            return
        self.file_path = path
        messagebox.showinfo("Saved", "File saved successfully.")

    def discard_edits(self):
        if self.original_blob is None:
            return
        if not messagebox.askyesno("Discard edits", "Discard ALL unsaved edits and revert to file state at open?"):
            return
        self.working_blob = self.original_blob
        self.edits.clear()
        self.refresh_parse()
        self._refresh_hex_view()

    # -----------------------------
    # Parsing & tree
    # -----------------------------
    def refresh_parse(self):
        if self.working_blob is None:
            return
        prev_offset = self._selected.offset if self._selected else None
        try:
            self.instances = parse_mrdf(self.working_blob, self.profile.defs)
        except Exception as e:
            messagebox.showerror("Parse failed", str(e))
            return

        pinfo = self.profile.label
        self.status_var.set(
            f"Profile: {pinfo} | Loaded: {self.file_path or '(unsaved)'} | Fields: {len(self.instances)} | Edits: {len(self.edits)}"
        )
        self._rebuild_tree()

        self._selected = None
        self._restore_selection_by_offset(prev_offset)

    def _rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._iid_by_offset.clear()

        filter_txt = self.filter_var.get().strip().lower()
        sections: Dict[str, List[MrdfFieldInstance]] = {}
        for inst in self.instances:
            if filter_txt:
                if filter_txt not in inst.definition.section.lower() and filter_txt not in inst.definition.name.lower():
                    continue
            sections.setdefault(inst.definition.section, []).append(inst)

        for section in sorted(sections.keys()):
            sid = self.tree.insert("", "end", text=section, open=True)
            for inst in sorted(sections[section], key=lambda x: x.offset):
                shown = self._value_to_string(inst.definition, self._current_value(inst))
                iid = self.tree.insert(
                    sid, "end",
                    text=inst.definition.name,
                    values=(shown, inst.definition.scalar, f"{inst.offset:#x}")
                )
                self._iid_by_offset[inst.offset] = iid

    def _current_value(self, inst: MrdfFieldInstance) -> Any:
        if inst.offset in self.edits:
            return self.edits[inst.offset]
        return inst.value

    def _value_to_string(self, d: MrdfFieldDef, v: Any) -> str:
        # Special handling for TyreAvailability bitmask
        if d.name == "TyreAvailability":
            byte_val = int(v) & 0xFF  # low byte is the mask
            parts = []
            for mask, label in TYRE_BITS:
                if byte_val & mask:
                    parts.append(label)
            if parts:
                return f"0x{byte_val:02X} ({', '.join(parts)})"
            return f"0x{byte_val:02X} (None)"

        
        if d.enum is not None:
            iv = int(v)
            label = d.enum.get(iv, "Unknown")
            return f"{iv} ({label})"
        if d.scalar == "float":
            return f"{float(v):.6g}"
        return str(int(v))

    def _on_select(self, _evt):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]

        inst = None
        for x in self.instances:
            if self._iid_by_offset.get(x.offset) == iid:
                inst = x
                break

        if inst is None:
            self._selected = None
            self.sel_title.set("(section)")
            self._set_meta("")
            self.apply_btn.configure(state="disabled")
            self.revert_btn.configure(state="disabled")
            return

        self._selected = inst
        d = inst.definition
        current = self._current_value(inst)

        self.sel_title.set(f"{self.profile.label} / {d.section} / {d.name}")
        meta = (
            f"Offset:    {d.offset:#x}\n"
            f"Type:      {d.scalar}\n"
            f"Raw bytes: {inst.raw.hex(' ')}\n"
            f"Value:     {self._value_to_string(d, current)}\n"
        )
        if d.notes:
            meta += f"\nNote: {d.notes}\n"
        self._set_meta(meta)

        self._editor_var.set(str(current if d.scalar != "float" else float(current)))
        if d.enum is not None:
            items = [f"{k} ({v})" for k, v in sorted(d.enum.items(), key=lambda kv: kv[0])]
            self.enum_combo["values"] = items
            iv = int(current)
            label = d.enum.get(iv, "Unknown")
            self._enum_choice_var.set(f"{iv} ({label})")
            self.enum_combo.grid()
        else:
            self.enum_combo.grid_remove()

        if d.name == "TyreAvailability":
            byte_val = int(current) & 0xFF
            for mask, var in self._bitmask_vars.items():
                var.set(bool(byte_val & mask))
            self._update_tyre_bit_displays()
            self.bitmask_frame.grid()
        else:
            self.bitmask_frame.grid_remove()


        self.apply_btn.configure(state="normal")
        self.revert_btn.configure(state="normal")

        self._highlight_selected_in_hex(inst)

    def _set_meta(self, s: str):
        self.meta_text.configure(state="normal")
        self.meta_text.delete("1.0", "end")
        self.meta_text.insert("1.0", s)
        self.meta_text.configure(state="disabled")

    # -----------------------------
    # Apply / revert (scalar)
    # -----------------------------
    def apply_edit(self):
        inst = self._selected
        if inst is None or self.working_blob is None:
            return

        d = inst.definition
        try:
            # Handle bitmask field (TyreAvailability)
            if d.name == "TyreAvailability":
                new_val = 0
                for mask, var in self._bitmask_vars.items():
                    if var.get():
                        new_val |= mask
            elif d.enum is not None and self._enum_choice_var.get().strip():
                s = self._enum_choice_var.get().split()[0]
                new_val = int(s, 10)
            else:
                s = self._editor_var.get().strip()
                if d.scalar == "float":
                    new_val = float(s)
                else:
                    new_val = int(s, 16) if s.lower().startswith("0x") else int(s, 10)
        except Exception as e:
            messagebox.showerror("Invalid edit", f"Could not parse value: {e}")
            return

        try:
            out = bytearray(self.working_blob)
            write_scalar(out, d.offset, d.scalar, new_val)
            self.working_blob = bytes(out)
            self.edits[d.offset] = new_val
        except Exception as e:
            messagebox.showerror("Write failed", str(e))
            return

        self.refresh_parse()
        self._refresh_hex_view()
        messagebox.showinfo("Applied", "Edit applied (in-place).")

    def revert_field(self):
        inst = self._selected
        if inst is None or self.original_blob is None or self.working_blob is None:
            return
        d = inst.definition
        if d.offset not in self.edits:
            return

        # Get the correct byte size for this field type
        _, n = _FMT[d.scalar]
        out = bytearray(self.working_blob)
        out[d.offset:d.offset+n] = self.original_blob[d.offset:d.offset+n]
        self.working_blob = bytes(out)
        del self.edits[d.offset]

        self.refresh_parse()
        self._refresh_hex_view()

    def _restore_selection_by_offset(self, offset: Optional[int]):
        """Re-select a field in the tree by MRDF offset after refresh."""
        if offset is None:
            return

        iid = self._iid_by_offset.get(offset)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
            # Manually trigger selection handler
            self._on_select(None)

    # -----------------------------
    # Hex viewer/editor
    # -----------------------------
    def _refresh_hex_view(self):
        if self.working_blob is None:
            self._set_hex_text("")
            self.hex_info_var.set("")
            return

        blob = self.working_blob
        self.hex_anchor = (self.hex_anchor // 16) * 16
        self.hex_anchor = clamp(self.hex_anchor, 0, max(0, len(blob) - 1))

        lines = format_hex_lines(blob, self.hex_anchor, self.hex_bytes_per_page, 16)
        self._hex_line_index.clear()
        for idx, line in enumerate(lines):
            off = int(line.split()[0], 16)
            self._hex_line_index[off] = idx

        self._set_hex_text("\n".join(lines) + ("\n" if lines else ""))

        end = min(len(blob), self.hex_anchor + self.hex_bytes_per_page)
        self.hex_info_var.set(f"{self.hex_anchor:08X} .. {end:08X}  (size {len(blob)} bytes)")

        if self._selected is not None:
            self._highlight_selected_in_hex(self._selected, refresh_only=True)

    def _set_hex_text(self, s: str):
        self.hex_text.configure(state="normal")
        self.hex_text.delete("1.0", "end")
        self.hex_text.insert("1.0", s)
        self.hex_text.configure(state="disabled")

    def hex_page(self, direction: int):
        if self.working_blob is None:
            return
        self.hex_anchor += direction * self.hex_bytes_per_page
        self.hex_anchor = clamp(self.hex_anchor, 0, max(0, len(self.working_blob) - 1))
        self._refresh_hex_view()

    def hex_jump(self):
        if self.working_blob is None:
            return
        s = self.jump_var.get().strip()
        try:
            off = int(s, 16) if s.lower().startswith("0x") else int(s, 16)
        except Exception:
            messagebox.showerror("Jump failed", "Enter a hex offset like 0xA4 or A4.")
            return
        off = clamp(off, 0, max(0, len(self.working_blob) - 1))
        self.hex_anchor = (off // 16) * 16
        self._refresh_hex_view()

    def _highlight_selected_in_hex(self, inst: MrdfFieldInstance, refresh_only: bool = False):
        self.hex_text.configure(state="normal")
        self.hex_text.tag_remove("sel_value", "1.0", "end")
        self.hex_text.configure(state="disabled")

        if self.working_blob is None:
            return

        start = inst.offset
        # Get the correct byte length for this field type
        _, length = _FMT[inst.definition.scalar]

        if not refresh_only:
            self.hex_anchor = (start // 16) * 16
            self.hex_anchor = clamp(self.hex_anchor - 16 * 4, 0, max(0, len(self.working_blob) - 1))
            self._refresh_hex_view()

        self._set_hex_target(start, length, f"{inst.definition.name} @ {start:08X} ({length} bytes)")
        payload = self.working_blob[start:start+length]
        self.hex_edit_var.set(payload.hex(" ").upper())

        self._tag_range_in_hex(start, length, "sel_value")
        self._see_offset(start)

    def _see_offset(self, off: int):
        line_off = (off // 16) * 16
        idx = self._hex_line_index.get(line_off)
        if idx is None:
            return
        self.hex_text.configure(state="normal")
        self.hex_text.see(f"{idx+1}.0")
        self.hex_text.configure(state="disabled")

    def _tag_range_in_hex(self, start: int, length: int, tag: str):
        if self.working_blob is None or length <= 0:
            return

        page_start = self.hex_anchor
        page_end = self.hex_anchor + self.hex_bytes_per_page
        sel_start = max(start, page_start)
        sel_end = min(start + length, page_end)
        if sel_end <= sel_start:
            return

        def hex_col(byte_i: int) -> int:
            return 10 + byte_i * 3

        self.hex_text.configure(state="normal")
        for off in range(sel_start, sel_end):
            line_off = (off // 16) * 16
            byte_i = off - line_off
            line_idx = self._hex_line_index.get(line_off)
            if line_idx is None:
                continue
            line_no = line_idx + 1
            c0 = hex_col(byte_i)
            c1 = c0 + 2
            self.hex_text.tag_add(tag, f"{line_no}.{c0}", f"{line_no}.{c1}")
        self.hex_text.configure(state="disabled")

    def _set_hex_target(self, start: Optional[int], length: Optional[int], label: str):
        self._hex_sel_start = start
        self._hex_sel_len = length
        self.hex_target_var.set(label)
        if start is None or length is None or self.working_blob is None:
            self.hex_apply_btn.configure(state="disabled")
            self.hex_revert_btn.configure(state="disabled")
        else:
            self.hex_apply_btn.configure(state="normal")
            self.hex_revert_btn.configure(state="normal")

    def _parse_hex_bytes(self, s: str) -> bytes:
        s = s.strip()
        if not s:
            return b""
        parts = s.replace(",", " ").split()
        try:
            return bytes(int(p, 16) for p in parts)
        except Exception:
            raise ValueError("Hex bytes must be like: 'DE AD BE EF' (space-separated)")

    def apply_hex_overwrite(self):
        if self.working_blob is None:
            return
        if self._hex_sel_start is None or self._hex_sel_len is None:
            return

        start = self._hex_sel_start
        n = self._hex_sel_len
        try:
            new_bytes = self._parse_hex_bytes(self.hex_edit_var.get())
        except Exception as e:
            messagebox.showerror("Hex overwrite failed", str(e))
            return

        if len(new_bytes) != n:
            messagebox.showerror(
                "Hex overwrite failed",
                f"Byte count mismatch: target is {n} bytes but you provided {len(new_bytes)} bytes."
            )
            return

        out = bytearray(self.working_blob)
        out[start:start+n] = new_bytes
        self.working_blob = bytes(out)

        self.refresh_parse()
        self._refresh_hex_view()
        messagebox.showinfo("Overwritten", f"Wrote {n} bytes at {start:08X} (in-place).")

    def revert_hex_overwrite(self):
        if self.original_blob is None or self.working_blob is None:
            return
        if self._hex_sel_start is None or self._hex_sel_len is None:
            return

        start = self._hex_sel_start
        n = self._hex_sel_len
        if start + n > len(self.original_blob):
            messagebox.showerror("Revert failed", "Selected range is out of bounds of original file.")
            return

        out = bytearray(self.working_blob)
        out[start:start+n] = self.original_blob[start:start+n]
        self.working_blob = bytes(out)

        self.refresh_parse()
        self._refresh_hex_view()
        messagebox.showinfo("Reverted", f"Reverted {n} bytes at {start:08X} to original.")


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    app = MrdfEditorApp()
    app.mainloop()