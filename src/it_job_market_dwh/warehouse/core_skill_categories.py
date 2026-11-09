"""Category refinements for normalized names in dw.dim_core_skill."""

from it_job_market_dwh.analytics.atomic import label_key


# Match complete labels, not substrings. Unlisted skills keep their source category.
_SKILLS_BY_CATEGORY = {
    "data_ai": (
        "AI", "Artificial Intelligence", "Agentic AI", "Generative AI", "GenAI",
        "LLM", "LLMs", "Large Language Model", "Large Language Models",
        "NLP", "Natural Language Processing", "Computer Vision",
        "Machine Learning", "Deep Learning", "MLOps",
        "Business Intelligence", "Power BI", "Data Analysis", "Data Science",
        "Data Modeling", "Data Modelling", "Data-driven", "ETL", "ELT",
    ),
    "data_streaming": ("Kafka", "Apache Kafka", "Event Streaming"),
    "database": ("SQL Server", "Microsoft SQL Server", "Azure SQL Database", "Microsoft Azure SQL Database"),
    "cloud": ("Cloud", "Cloud-native Architecture"),
    "devops": ("Grafana", "Prometheus"),
    "enterprise": ("CRM", "ERP", "Microsoft Dynamics 365", "Power Automate"),
    "methodology": ("Waterfall Methodology",),
    "testing": ("Unit Test", "Unit Testing"),
    "design": ("Design", "Design Systems", "Information Architecture", "Product Design"),
    "markup_language": ("HTML5",),
    "stylesheet_language": ("Sass", "SCSS"),
    "operating_system": ("Unix",),
    "tool": ("Jira",),
    "scripting": ("Bash", "Bash Shell", "PowerShell"),
    "embedded": ("AUTOSAR", "Embedded", "Embedded C"),
    "mobile_development": ("Android", "iOS", "Mobile Apps"),
    "integration": ("API", "REST API", "GraphQL"),
    "architecture": (
        "Clean Architecture", "Enterprise Architecture", "Microservices",
        "Microservices Architecture", "SOLID Principles", "Software Architecture",
        "Solution Architecture", "System Architecture", "TOGAF",
    ),
    "business_analysis": ("Business Analysis", "BRD", "Functional Specifications", "SRS", "Stakeholder Management"),
    "product_management": ("Product Management", "Product Roadmap", "Product Strategy"),
    "it_governance": ("IT Audit", "IT Governance"),
    "it_operations": ("IT Infrastructure", "IT Support", "System Admin"),
    "documentation": ("Technical Writing",),
    "game_development": ("Unity",),
}
_CATEGORY_BY_NAME = {
    label_key(name): category
    for category, names in _SKILLS_BY_CATEGORY.items()
    for name in names
}


def core_skill_category(name: str, source_category: str) -> str:
    """Use a known category when available; otherwise keep the source label."""
    key = label_key(name) if name.strip() else ""
    return _CATEGORY_BY_NAME.get(key, source_category)
