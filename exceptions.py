class BusinessRuleError(Exception):
	"""Raised when an operation violates a retail business rule."""


class NotFoundError(BusinessRuleError):
	"""Raised when a requested domain record does not exist."""
