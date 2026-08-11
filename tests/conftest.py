# -*- coding: utf-8 -*-
"""pytest 全局 fixtures。"""
import os
import sys
import tempfile
import shutil

import pytest

# 确保项目根目录在 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture
def tmp_data_dir():
    """临时数据目录，测试结束后自动清理。"""
    d = tempfile.mkdtemp(prefix="rpaapp_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)
