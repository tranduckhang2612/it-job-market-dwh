"""Project taxonomy for dw.dim_role, keyed by normalized role name."""

from it_job_market_dwh.analytics.atomic import label_key


DEFAULT_ROLE_CATEGORY = "Software Engineer"
# Keep the category labels exactly as specified by the project.
_SPECIALIZATIONS = {
    "Data/AI": (
        "Data Architect", "Data Engineer", "Data Scientist", "Data Analyst",
        "Machine Learning Engineer", "AI Engineer", "Business Intelligence (BI) Developer",
        "Big Data Engineer", "Data Governance Specialist", "Data Visualization Engineer",
        "Database Administrator (DBA)", "Data Warehouse Engineer",
        "ETL Developer (Extract. Transform. Load)", "Data Modeler",
        "Natural Language Processing (NLP) Engineer",
    ),
    "Security": (
        "Security Engineer", "Penetration Tester", "Cybersecurity Engineer",
        "Vulnerability Analyst", "DevSecOps Engineer", "Security Test Engineer",
    ),
    "Embbed": ("Firmware Developer", "Embedded Systems Developer"),
    "Web/App": (
        "Front-end Developer", "Back-end Developer", "Full-stack Developer",
        "Mobile Application Developer", "iOS Developer", "Android Developer",
        "React Native Developer", "Flutter Developer", "Kotlin Developer",
        "JavaScript Developer", "API Developer", "HTML5 Developer",
    ),
}
_CATEGORY_BY_ROLE = {
    label_key(name): category
    for category, names in _SPECIALIZATIONS.items()
    for name in names
}


def role_category(name: str) -> str:
    """Unclassified roles use Software Engineer, never Other."""
    key = label_key(name) if name.strip() else ""
    return _CATEGORY_BY_ROLE.get(key, DEFAULT_ROLE_CATEGORY)
