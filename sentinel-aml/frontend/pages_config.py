"""Role-based navigation (mirrors API RBAC; the API remains the enforcement point)."""
ROLE_PAGES = {
    "analyst": ["Overview", "Alert queue", "Investigations", "Knowledge search"],
    "investigator": ["Overview", "Alert queue", "Investigations", "Approvals", "Knowledge search"],
    "mlro": ["Overview", "Alert queue", "Investigations", "Approvals", "Knowledge search", "Model and AI operations", "Audit trail"],
    "auditor": ["Overview", "Alert queue", "Investigations", "Model and AI operations", "Audit trail"],
    "admin": ["Knowledge search", "Model and AI operations", "Audit trail"],
}
