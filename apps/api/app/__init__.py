"""Aether Studio API package."""

# Allow the duplicated Intel OpenMP runtime BEFORE any heavy library (numpy/MKL,
# onnxruntime, torch) initializes OpenMP. anaconda's numpy ships its own libiomp5;
# the OCR engine and ZeroTTS both load onnxruntime, which can load
# another, and on Windows the duplicate aborts the whole process ("OMP: Error #15").
# Setting this lazily (after numpy is already imported) is too late, so it must live
# at the very top of the package import — the earliest point every entrypoint runs.
import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
