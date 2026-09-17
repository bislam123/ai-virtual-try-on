"""Regression tests for fashn_vton.pipeline._dwpose_device_string.

Root cause this guards against: TryOnPipeline._setup_hp_model() used to pass
a bare "cuda" (no device index) to MediaPipeBodyParser -> DWposeDetector ->
Wholebody, whose __init__ always does int(device.split(":")[-1]) for any
device starting with "cuda" -- raising ValueError on a real CUDA machine
(e.g. Google Colab's T4). _setup_pose_model() already built the device
string correctly; _setup_hp_model() diverged. Both now share this one
helper so they cannot diverge again.
"""

import torch

from fashn_vton.pipeline import _dwpose_device_string


def test_cpu_device_stays_cpu():
    assert _dwpose_device_string(torch.device("cpu")) == "cpu"


def test_bare_cuda_device_gets_explicit_index_zero():
    # torch.device("cuda") has .index == None -- this is exactly the input
    # that used to reach Wholebody as a bare "cuda" and crash.
    assert _dwpose_device_string(torch.device("cuda")) == "cuda:0"


def test_cuda_device_with_explicit_index_zero_is_preserved():
    assert _dwpose_device_string(torch.device("cuda:0")) == "cuda:0"


def test_cuda_device_with_nonzero_index_is_preserved():
    assert _dwpose_device_string(torch.device("cuda:1")) == "cuda:1"
