# Trimmed for the ShotBook renderer: inference only. The upstream __init__ also
# imported the *training* pipelines (self_forcing_training, teacher_forcing_training,
# bidirectional_training), which pull wandb/trainer deps we don't install in the
# renderer venv. cf_streaming.py only needs CausalInferencePipeline.
from .causal_inference import CausalInferencePipeline

__all__ = ["CausalInferencePipeline"]
