"""ML Platform (v5.1) — feature store, model registry, training & inference.

Public API: FeatureStore, ModelRegistry, TrainingPipeline, InferenceService.
"""
from ml_platform.feature_store import FeatureStore
from ml_platform.registry import ModelRegistry
from ml_platform.training import InferenceService, TrainingPipeline

__all__ = ["FeatureStore", "ModelRegistry", "TrainingPipeline", "InferenceService"]
