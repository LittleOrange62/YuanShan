# -*- coding: utf-8 -*-
"""调试 TQ 初始化，打印详细错误"""
import sys
import os
import ctypes
import json

TQCENTER_DIR = r"D:\new_tdx64\PYPlugins\sys"
if TQCENTER_DIR not in sys.path:
    sys.path.insert(0, TQCENTER_DIR)

global_dll_path = r"D:\new_tdx64\PYPlugins\TPythClient.dll"
print(f"DLL路径: {global_dll_path}, 存在: {os.path.exists(global_dll_path)}")

try:
    dll = ctypes.CDLL(str(global_dll_path))
    dll.InitConnect.restype = ctypes.c_char_p
    dll.GetTdxDataStr.restype = ctypes.c_char_p
    dll.CloseConnect.restype = None

    file_name = os.path.abspath(__file__).encode('utf-8')
    dll_path_str = b''
    run_mode = 1
    re_initialized = False
    pyver = 311

    print("调用 InitConnect...")
    ptr = dll.InitConnect(file_name, dll_path_str, run_mode, pyver, re_initialized)
    if len(ptr) <= 0:
        print("InitConnect 返回空指针!")
    else:
        result_str = ptr.decode('utf-8')
        print(f"InitConnect 返回: {result_str}")
        try:
            j = json.loads(result_str)
            print(f"解析JSON: ErrorId={j.get('ErrorId')}, Error={j.get('Error')}, run_id={j.get('run_id')}")
        except Exception as e:
            print(f"JSON解析失败: {e}")
except Exception as e:
    print(f"加载DLL或调用失败: {e}")
