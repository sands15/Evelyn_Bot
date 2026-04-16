class EvelynVoiceError(Exception):
    """Base error for evelyn_voice."""


class VoiceNotReadyError(EvelynVoiceError):
    """Voice connection is not ready yet."""


class DaveNotReadyError(EvelynVoiceError):
    """DAVE session is not ready yet."""


class ReceiveNotStartedError(EvelynVoiceError):
    """Receive pipeline has not started."""