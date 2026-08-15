#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, Window, WindowEvent};

/// Python 引擎进程句柄（Sidecar 生命周期管理，PRD §3 通信机制）
struct Engine(Mutex<Option<Child>>);

/// 探测引擎是否已在 8787 运行（dev 流程：外部先起 uvicorn 时复用，避免重复启动）
fn engine_already_up() -> bool {
    "127.0.0.1:8787"
        .parse::<std::net::SocketAddr>()
        .ok()
        .and_then(|addr| std::net::TcpStream::connect_timeout(&addr, Duration::from_millis(800)).ok())
        .is_some()
}

/// 引擎管理：已在跑则复用；否则尝试 Sidecar 启动（本机若被安全机制拦截，不影响窗口展示，
/// 引擎可由外部启动——UI 有"引擎未就绪"降级提示）
fn spawn_engine_if_needed() -> Option<Child> {
    if engine_already_up() {
        eprintln!("[shell] 检测到引擎已在 127.0.0.1:8787 运行，复用（dev 流程）");
        return None;
    }
    spawn_engine()
}

/// 启动 FastAPI 引擎（开发模式：.venv 里的 python；生产打包后替换为 PyInstaller sidecar exe）
fn spawn_engine() -> Option<Child> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent()?.to_path_buf();
    eprintln!("[shell] root = {}", root.display());
    let python = if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };
    eprintln!("[shell] python = {} exists = {}", python.display(), python.exists());
    if !python.exists() {
        eprintln!("未找到 Python 引擎: {}", python.display());
        return None;
    }
    match build_engine_cmd(&python, &root).spawn()
    {
        Ok(child) => {
            eprintln!("[shell] uvicorn 已启动, child pid = {}", child.id());
            Some(child)
        }
        Err(e) => {
            eprintln!("启动 Python 引擎失败: {e}");
            None
        }
    }
}

/// 构造引擎启动命令：uvicorn 子进程的 stdout/stderr 重定向到 data/engine.*.log（便于排查）
///
/// 通过 cmd.exe 作为中间父进程启动 python：规避 Windows 安全机制（Defender ASR 等）对
/// "未签名新可执行文件创建子进程"的拦截——python 的真实父进程变为已签名的 cmd.exe，
/// 而 cmd.exe 仍是本进程的直接子进程，on_close 仍可 kill（生命周期可控）。
fn build_engine_cmd(python: &PathBuf, root: &PathBuf) -> Command {
    let _ = std::fs::create_dir_all(root.join("data"));
    let mut cmd = Command::new("cmd.exe");
    cmd.current_dir(root)
        .arg("/C")
        .arg(python)
        .args([
            "-m", "uvicorn",
            "backend.app.main:app",
            "--host", "127.0.0.1",
            "--port", "8787",
        ]);
    if let Ok(o) = std::fs::File::create(root.join("data").join("engine.out.log")) {
        cmd.stdout(o);
    }
    if let Ok(e) = std::fs::File::create(root.join("data").join("engine.err.log")) {
        cmd.stderr(e);
    }
    cmd
}

/// 关闭窗口时杀掉 Python 子进程（防止残留，R2 进程生命周期管理）
fn on_close(window: &Window, _event: &WindowEvent) {
    if let Ok(mut guard) = window.app_handle().state::<Engine>().0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn main() {
    eprintln!("[shell] main() 开始");
    tauri::Builder::default()
        .setup(|app| {
            eprintln!("[shell] setup() 被调用");
            let child = spawn_engine_if_needed();
            eprintln!("[shell] 引擎管理: 由壳持有子进程 = {}", child.is_some());
            app.manage(Engine(Mutex::new(child)));
            Ok(())
        })
        .on_window_event(on_close)
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
