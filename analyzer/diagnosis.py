"""Data model for explainable diagnoses.

Three independent axes (never merged into one number):
  state       how strong the *evidence of contention* is
                ELEVATED  resource is busy/used but nothing is waiting (not a bottleneck claim)
                POTENTIAL some contention evidence, incomplete or contradicted
                CONFIRMED sustained contention by the engine's criteria
  severity    magnitude: INFO | WARNING | CRITICAL (how far past thresholds, for the full window)
  confidence  LOW | MEDIUM | HIGH  - how well independent indicators agree (rules documented
              in docs/DETECTION_MODEL.md); `evidence_ratio` shows the raw agree/available count.
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional

STATES = ("ELEVATED", "POTENTIAL", "CONFIRMED")
STATE_LABEL = {"ELEVATED": "Elevated utilisation (no contention evidence)",
               "POTENTIAL": "Potential resource pressure",
               "CONFIRMED": "Confirmed sustained contention"}
CLASSES = ("stall", "saturation", "utilization", "supporting")


@dataclass
class Evidence:
    name: str
    cls: str                    # stall | saturation | utilization | supporting
    status: str                 # present | absent | unavailable
    detail: str
    method: Optional[str] = None   # static | baseline | both (only when present)
    value: Optional[float] = None


@dataclass
class Diagnosis:
    resource: str               # CPU | Memory | Disk I/O | Process
    state: str
    severity: str
    confidence: str
    evidence_ratio: str
    headline: str
    likely_cause: str
    recommendation: str
    device: Optional[str] = None
    duration_s: float = 0.0
    method: str = "static"      # static | baseline | static+baseline
    evidence: List[Evidence] = field(default_factory=list)
    contradicting: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    responsible: Optional[dict] = None     # attribution dict (verdict + contributors)
    responsible_text: str = ""

    @property
    def key(self):
        return (self.resource, self.device)

    def to_alert(self):
        """Dict for CSV/JSON.  Keeps the legacy keys (resource, severity, diagnosis,
        recommendation) and adds the explainable fields."""
        d = asdict(self)
        d["diagnosis"] = self.headline
        d["state_label"] = STATE_LABEL[self.state]
        return d
