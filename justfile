set positional-arguments := true
set shell := ["python", "-c"]

# Python -c 的 argv[0] 是 -c，Just 在 argv[1] 放任务名，实参从 argv[2] 开始。

# 仅列出任务，不隐式构建或发布。
default:
    import subprocess; subprocess.run(["just", "--list"], check=True)

test-native-bridge *args:
    import subprocess, sys; subprocess.run([sys.executable, "scripts/check_native_bridge.py", *sys.argv[2:]], check=True)

sdist out="dist/source":
    import subprocess, sys; subprocess.run([sys.executable, "scripts/build_dist.py", "sdist", *sys.argv[2:]], check=True)

verify-source sdist receipts:
    import subprocess, sys; subprocess.run([sys.executable, "scripts/build_dist.py", "verify-source", *sys.argv[2:]], check=True)

# 在 Linux Docker 宿主中使用官方 manylinux 容器。
wheel-linux sdist out python-version:
    import subprocess, sys; subprocess.run([sys.executable, "scripts/build_dist.py", "container", *sys.argv[2:4], "--python-version", sys.argv[4]], check=True)

# 无容器的 Linux 构建；调用者显式准备 Zig 与解释器。
wheel-linux-zig sdist out +interpreters:
    import subprocess, sys; subprocess.run([sys.executable, "scripts/build_dist.py", "linux-zig", *sys.argv[2:4], "--interpreter", *sys.argv[4:]], check=True)

# macOS 或 Windows 原生构建，不安装或猜测宿主工具链。
wheel-native sdist out target +interpreters:
    import subprocess, sys; subprocess.run([sys.executable, "scripts/build_dist.py", "native", *sys.argv[2:4], "--target", sys.argv[4], "--interpreter", *sys.argv[5:]], check=True)

verify-wheel wheel receipts:
    import subprocess, sys; subprocess.run([sys.executable, "scripts/verify_wheel.py", "--wheel", sys.argv[2], "--receipts", sys.argv[3]], check=True)

check-dist dist receipts:
    import subprocess, sys; subprocess.run([sys.executable, "scripts/publish_dist.py", "check", *sys.argv[2:]], check=True)

publish dist receipts:
    import subprocess, sys; subprocess.run([sys.executable, "scripts/publish_dist.py", "upload", *sys.argv[2:]], check=True)
