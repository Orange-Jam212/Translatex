"""
一键打包：将 latex.py 封装为 macOS .app，自动创建桌面快捷方式。

用法：
  python3 package.py

依赖：
  pip install pyinstaller PySide6 PySide6-WebEngine
"""

import os
import sys
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "translatex"
DESKTOP = os.path.expanduser("~/Desktop")

# 需要打入 .app 的资源文件
RESOURCES = [
    "pdf.min.js",
    "pdf.worker.min.js",
    "tray_icon.png",
    "pdf.png",
    "app_icon.png",
    "logo.png",
]


def step(msg):
    print(f"\n{'='*50}\n  {msg}\n{'='*50}")


def package():
    step("检查资源文件")
    for f in RESOURCES:
        path = os.path.join(HERE, f)
        if not os.path.exists(path):
            print(f"  ❌ 缺失: {f}")
            sys.exit(1)
        print(f"  ✅ {f}")

    icon = os.path.join(HERE, "logo.icns")
    if not os.path.exists(icon):
        print(f"  ⚠️  缺少 logo.icns，将使用默认图标")

    step("生成 PyInstaller spec 文件")
    datas_block = ""
    for f in RESOURCES:
        datas_block += f"        ('{os.path.join(HERE, f)}', '.'),\n"

    icon_block = ""
    if os.path.exists(icon):
        icon_block = f"    icon='{icon}',\n"

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
{datas_block}]
binaries = []
hiddenimports = []
tmp_ret = collect_all('PySide6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['{os.path.join(HERE, "latex.py")}'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='{APP_NAME}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
{icon_block})
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='{APP_NAME}',
)
app = BUNDLE(
    coll,
    name='{APP_NAME}.app',
{icon_block}    bundle_identifier=None,
)
'''

    spec_path = os.path.join(HERE, f"{APP_NAME}.spec")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)
    print(f"  ✅ 已生成: {spec_path}")

    step("清理旧构建产物")
    for d in ["build", "dist"]:
        p = os.path.join(HERE, d)
        if os.path.exists(p):
            shutil.rmtree(p)
            print(f"  🧹 已删除: {d}")

    step("开始打包（可能需要几分钟）")
    result = subprocess.run(
        ["pyinstaller", spec_path, "--noconfirm", "--clean"],
        cwd=HERE,
        capture_output=False,  # 实时输出以便看进度
    )

    if result.returncode != 0:
        print("\n  ❌ 打包失败，请检查上方错误信息")
        sys.exit(1)

    app_path = os.path.join(HERE, "dist", f"{APP_NAME}.app")
    if not os.path.exists(app_path):
        print(f"\n  ❌ 未找到 .app: {app_path}")
        sys.exit(1)

    step("创建桌面快捷方式")
    alias_path = os.path.join(DESKTOP, f"{APP_NAME}.app")
    # 删除旧快捷方式
    if os.path.exists(alias_path):
        if os.path.islink(alias_path):
            os.unlink(alias_path)
        else:
            shutil.rmtree(alias_path)
        print(f"  🧹 已删除旧快捷方式")

    os.symlink(app_path, alias_path)
    print(f"  ✅ 桌面快捷方式: {alias_path}")

    step("清理构建中间文件")
    build_dir = os.path.join(HERE, "build")
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    if os.path.exists(spec_path):
        os.remove(spec_path)
    print(f"  🧹 中间文件已清理")

    step("🎉 打包完成！")
    print(f"  App 位置: {app_path}")
    print(f"  桌面快捷方式: {alias_path}")
    print(f"  双击 .app 即可运行")


if __name__ == "__main__":
    package()
