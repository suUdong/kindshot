"""Domain-specific exception hierarchy for Kindshot."""


class KindshotError(Exception):
    """Base exception for all Kindshot errors."""


# --- KIS API ---

class KisApiError(KindshotError):
    """KIS API call failed."""

    def __init__(self, message: str, *, endpoint: str = "", status_code: int = 0):
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(message)


class KisAuthError(KisApiError):
    """KIS authentication/token error."""


class KisRateLimitError(KisApiError):
    """KIS API rate limit exceeded."""


# --- Collector ---

class CollectorError(KindshotError):
    """Data collection failed."""

    def __init__(self, message: str, *, date: str = ""):
        self.date = date
        super().__init__(message)


class CollectorNewsError(CollectorError):
    """News collection failed for a specific date."""


class CollectorPriceError(CollectorError):
    """Price data collection failed."""


# --- Replay ---

class ReplayError(KindshotError):
    """Replay execution failed."""

    def __init__(self, message: str, *, date: str = ""):
        self.date = date
        super().__init__(message)


class ReplayDataError(ReplayError):
    """Required replay data missing or corrupt."""


# --- Pipeline ---

class PipelineError(KindshotError):
    """Event processing pipeline error."""


class EventProcessingError(PipelineError):
    """Failed to process a specific event."""

    def __init__(self, message: str, *, event_id: str = ""):
        self.event_id = event_id
        super().__init__(message)
