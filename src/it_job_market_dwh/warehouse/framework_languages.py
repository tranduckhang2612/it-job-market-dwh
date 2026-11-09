"""Primary ecosystem language for known framework labels in the warehouse."""

from it_job_market_dwh.analytics.atomic import label_key


# Some entries support other languages. These are representative labels for
# reporting, not a claim about the language required by an individual posting.
_FRAMEWORKS = {
    "C#": (".NET", ".NET Core", "dotnet", "dotnet core"),
    "Dart": ("Flutter",),
    "Java": ("Spring", "Spring Boot"),
    "JavaScript": (
        "Express", "Next.js", "jQuery", "React", "React.js", "ReactJS",
        "React Native", "Vue.js", "Vue", "VueJS", "Node.js", "Node JS", "NodeJS",
    ),
    "PHP": ("Laravel",),
    "Python": ("Django", "FastAPI", "Flask", "PySpark", "PyTorch", "TensorFlow"),
    "TypeScript": ("Angular", "NestJS"),
}
_LANGUAGE_BY_NAME = {
    label_key(name): language
    for language, frameworks in _FRAMEWORKS.items()
    for name in frameworks
}


def framework_language(name: str) -> str | None:
    """Leave architectural patterns (e.g. MVVM) and unknown names unassigned."""
    return _LANGUAGE_BY_NAME.get(label_key(name)) if name.strip() else None
