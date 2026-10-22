from importlib import import_module

from app.detection.base import AnomalyDetector


class DetectorLoadError(RuntimeError):
    pass


def resolve_detector_class(path: str) -> type[AnomalyDetector]:
    """Resolve ``"package.module:ClassName"`` to an ``AnomalyDetector`` subclass."""
    module_name, _, class_name = path.partition(":")
    if not module_name or not class_name:
        raise DetectorLoadError(f"Expected 'module:ClassName', got {path!r}")
    try:
        detector_class = getattr(import_module(module_name), class_name)
    except ModuleNotFoundError as error:
        hint = {
            "sklearn": "ml",
            "joblib": "ml",
        }
        extra = hint.get((error.name or "").split(".")[0])
        if extra:
            raise DetectorLoadError(
                f"{path!r} needs '{error.name}'. "
                f'Install it: pip install -e ".[{extra}]"',
            ) from error
        raise DetectorLoadError(f"Cannot import {path!r}: {error}") from error
    except (ImportError, AttributeError) as error:
        raise DetectorLoadError(f"Cannot import {path!r}: {error}") from error
    if not (
        isinstance(detector_class, type) and issubclass(detector_class, AnomalyDetector)
    ):
        raise DetectorLoadError(f"{path!r} is not an AnomalyDetector subclass")
    return detector_class
