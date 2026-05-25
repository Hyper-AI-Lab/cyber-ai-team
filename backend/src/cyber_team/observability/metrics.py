import time
from collections import defaultdict
from threading import Lock


class MetricsService:
    def __init__(self) -> None:
        self._started_at = time.time()
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            dict[str, float | list[float]],
        ] = defaultdict(self._new_histogram)
        self._http_buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]

    def record_http_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        labels = {
            "method": method,
            "path": self._normalize_path(path),
            "status_code": str(status_code),
        }
        self.increment("cyberteam_http_requests_total", labels)
        self.observe("cyberteam_http_request_duration_seconds", duration_seconds, labels)

    def record_audit_event(self, event_type: str, outcome: str) -> None:
        self.increment(
            "cyberteam_audit_events_total",
            {"event_type": event_type, "outcome": outcome},
        )

    def record_authorization_decision(
        self,
        allowed: bool,
        resource_type: str,
        action: str,
        source: str,
    ) -> None:
        self.increment(
            "cyberteam_authorization_decisions_total",
            {
                "decision": "allowed" if allowed else "denied",
                "resource_type": resource_type,
                "action": action,
                "source": source,
            },
        )

    def increment(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        with self._lock:
            self._counters[(name, self._label_tuple(labels))] += value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = (name, self._label_tuple(labels))
        with self._lock:
            histogram = self._histograms[key]
            histogram["count"] = float(histogram["count"]) + 1
            histogram["sum"] = float(histogram["sum"]) + value
            buckets = histogram["buckets"]
            if isinstance(buckets, list):
                for index, bucket in enumerate(self._http_buckets):
                    if value <= bucket:
                        buckets[index] += 1

    def render_prometheus(self) -> str:
        lines = [
            "# HELP cyberteam_up Cyber-Team API process availability.",
            "# TYPE cyberteam_up gauge",
            "cyberteam_up 1",
            "# HELP cyberteam_uptime_seconds Cyber-Team API process uptime in seconds.",
            "# TYPE cyberteam_uptime_seconds gauge",
            f"cyberteam_uptime_seconds {time.time() - self._started_at:.6f}",
        ]
        with self._lock:
            lines.extend(self._render_counters())
            lines.extend(self._render_histograms())
        return "\n".join(lines) + "\n"

    def _render_counters(self) -> list[str]:
        lines: list[str] = []
        emitted_types: set[str] = set()
        for (name, label_tuple), value in sorted(self._counters.items()):
            if name not in emitted_types:
                lines.append(f"# TYPE {name} counter")
                emitted_types.add(name)
            lines.append(f"{name}{self._format_labels(label_tuple)} {value:.6f}")
        return lines

    def _render_histograms(self) -> list[str]:
        lines: list[str] = []
        emitted_types: set[str] = set()
        for (name, label_tuple), histogram in sorted(self._histograms.items()):
            if name not in emitted_types:
                lines.append(f"# TYPE {name} histogram")
                emitted_types.add(name)
            buckets = histogram["buckets"]
            if isinstance(buckets, list):
                for bucket, count in zip(self._http_buckets, buckets):
                    bucket_labels = tuple(sorted((*label_tuple, ("le", str(bucket)))))
                    lines.append(
                        f"{name}_bucket{self._format_labels(bucket_labels)} {count:.6f}"
                    )
            inf_labels = tuple(sorted((*label_tuple, ("le", "+Inf"))))
            lines.append(
                f"{name}_bucket{self._format_labels(inf_labels)} "
                f"{float(histogram['count']):.6f}"
            )
            lines.append(
                f"{name}_count{self._format_labels(label_tuple)} "
                f"{float(histogram['count']):.6f}"
            )
            lines.append(
                f"{name}_sum{self._format_labels(label_tuple)} "
                f"{float(histogram['sum']):.6f}"
            )
        return lines

    def _new_histogram(self) -> dict[str, float | list[float]]:
        return {"count": 0.0, "sum": 0.0, "buckets": [0.0 for _ in self._http_buckets]}

    @staticmethod
    def _label_tuple(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((key, str(value)) for key, value in (labels or {}).items()))

    @staticmethod
    def _format_labels(label_tuple: tuple[tuple[str, str], ...]) -> str:
        if not label_tuple:
            return ""
        rendered = ",".join(
            f'{key}="{MetricsService._escape_label(value)}"'
            for key, value in label_tuple
        )
        return "{" + rendered + "}"

    @staticmethod
    def _escape_label(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

    @staticmethod
    def _normalize_path(path: str) -> str:
        parts = []
        for part in path.strip("/").split("/"):
            if not part:
                continue
            if MetricsService._looks_like_identifier(part):
                parts.append("{id}")
            else:
                parts.append(part)
        return "/" + "/".join(parts) if parts else "/"

    @staticmethod
    def _looks_like_identifier(value: str) -> bool:
        if len(value) >= 24 and any(char.isdigit() for char in value):
            return True
        if len(value) >= 8 and "-" in value:
            return True
        return False
