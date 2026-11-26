"""Expected workflow failures; libraries never choose process exit codes."""


class WorkflowError(ValueError):
    code = "invalid_input"


class UnsupportedVersion(WorkflowError):
    code = "unsupported_version"


class RevisionConflict(WorkflowError):
    code = "revision_conflict"


class EvidenceError(WorkflowError):
    code = "invalid_evidence"


class PolicyDenied(WorkflowError):
    code = "policy_denied"


class StoreBusy(WorkflowError):
    code = "store_busy"


class StoreRecoveryRequired(WorkflowError):
    code = "store_recovery_required"


class BudgetExhausted(WorkflowError):
    code = "budget_exhausted"
