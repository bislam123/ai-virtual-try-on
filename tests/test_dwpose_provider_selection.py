"""Tests for fashn_vton.dwpose.wholebody.Wholebody's ONNX execution-provider
selection. This is vendored third-party code (see docs/DEVELOPMENT.md's
Colab/GPU section), not our own, but its provider-selection behavior directly
determines whether DWPose ever silently runs on the wrong device -- so it is
covered here rather than left unverified.

Uses the real DWPose weights already present in this repo's ai/models/ (same
fixture path convention as tests/test_mediapipe_body_parser.py). No network
access, no mocking of onnxruntime itself: these assert against the real
onnxruntime build actually installed, whatever it is.
"""

import onnxruntime as ort

from fashn_vton.dwpose.wholebody import Wholebody

DWPOSE_DIR = __file__.rsplit("tests", 1)[0] + "ai/models/fashn-vton-1.5/dwpose"

CUDA_AVAILABLE = "CUDAExecutionProvider" in ort.get_available_providers()


def test_cpu_device_selects_only_cpu_provider():
    pose = Wholebody(checkpoints_dir=DWPOSE_DIR, device="cpu")
    assert pose.session_det.get_providers() == ["CPUExecutionProvider"]
    assert pose.session_pose.get_providers() == ["CPUExecutionProvider"]


def test_cuda_device_request_never_crashes_and_degrades_correctly():
    """device="cuda:0" must construct successfully whether or not the
    installed onnxruntime build actually has a CUDA backend: with
    onnxruntime-gpu, CUDAExecutionProvider must actually be selected; with
    plain onnxruntime (this repo's CPU-only local dev default), onnxruntime
    must silently degrade to CPUExecutionProvider rather than raising.
    """
    pose = Wholebody(checkpoints_dir=DWPOSE_DIR, device="cuda:0")
    actual_providers = pose.session_det.get_providers()
    if CUDA_AVAILABLE:
        assert "CUDAExecutionProvider" in actual_providers
    else:
        assert actual_providers == ["CPUExecutionProvider"]
