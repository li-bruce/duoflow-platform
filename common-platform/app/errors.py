class PlatformError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        self.code, self.message, self.status, self.details = code, message, status, details
        super().__init__(message)


def require(condition, code, message, status=400):
    if not condition:
        raise PlatformError(code, message, status)
