"""Internal exceptions; translated to ToolError at tool boundaries."""


class ToolPipeError(Exception):
    """Base class for ToolPipe errors."""


class ResultNotFoundError(ToolPipeError):
    pass


class ResultExpiredError(ToolPipeError):
    pass


class UnsupportedResultTypeError(ToolPipeError):
    pass


class SelectionError(ToolPipeError):
    pass


class StorageLimitError(ToolPipeError):
    pass


class InvalidPipeTargetError(ToolPipeError):
    pass


class ConfigurationError(ToolPipeError):
    pass
