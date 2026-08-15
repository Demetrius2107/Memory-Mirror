#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, Window, WindowEvent};

/// Python 引擎进程句柄（Sidecar 生命周期管理，PRD §3 通信机制）
struct Engine(Mutex<Option<Child>>);

/// 启动 FastAPI 引擎（开发模式：.venv 里的 python；生产打包后替换为 PyInstaller sidecar exe）
fn spawn_engine() -> Option<Child> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent()?.to_path_buf();
    let python = if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };
    if !python.exists() {
        eprintln!("未找到 Python 引擎: {}", python.display());
        return None;
    }
    match Command::new(python)
        .current_dir(&root)
        .args([
            "-m", "uvicorn",
            "backend.app.main:app",
            "--host", "127.0.0.1",
            "--port", "8787",
        ])
        .spawn()
    {
        Ok(child) => Some(child),
        Err(e) => {
            eprintln!("启动 Python 引擎失败: {e}");
            None
        }
    }
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
    tauri::Builder::default()
        .setup(|app| {
            app.manage(Engine(Mutex::new(spawn_engine())));
            Ok(())
        })
        .on_window_event(on_close)
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
