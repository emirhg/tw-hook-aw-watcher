mod log;
mod config;
mod taskwarrior;
mod activitywatch;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use std::thread;

fn main() {
    // Load config
    let cfg = match config::load() {
        Ok(c) => c,
        Err(e) => {
            log::log_error(&format!("Fatal: {}", e));
            std::process::exit(1);
        }
    };

    // Install signal handler for clean shutdown
    let shutdown = Arc::new(AtomicBool::new(false));
    {
        let shutdown = shutdown.clone();
        ctrlc::set_handler(move || {
            shutdown.store(true, Ordering::SeqCst);
        }).expect("Error installing signal handler");
    }

    // Create ActivityWatch client
    let aw_client = match activitywatch::AwTaskWatcherClient::new(&cfg) {
        Ok(c) => c,
        Err(e) => {
            log::log_error(&format!("Fatal: failed to create AW client: {}", e));
            std::process::exit(1);
        }
    };

    log::log_info(&format!("Starting aw-taskwarrior, bucket={}", aw_client.bucket_id()));

    // Ensure bucket exists (retry with shutdown signal honored)
    if !aw_client.ensure_bucket_with_shutdown_check(&shutdown) {
        log::log_info("Shutdown requested during startup, exiting");
        std::process::exit(0);
    }

    // Initialize last known task to the default task
    let mut last_known_task = taskwarrior::ActiveTask::default_task();

    // Main poll loop
    while !shutdown.load(Ordering::SeqCst) {
        // Query TaskWarrior for active task
        let task = match taskwarrior::query_active_task() {
            Ok(t) => {
                last_known_task = t.clone();
                t
            }
            Err(e) => {
                log::log_error(&format!("task query failed, reusing last known task: {}", e));
                last_known_task.clone()
            }
        };

        // Select pulsetime based on whether task has offline tag
        let pulsetime = activitywatch::select_pulsetime(&task, &cfg);

        // Send heartbeat to ActivityWatch
        if let Err(e) = aw_client.send_heartbeat(&task, pulsetime) {
            log::log_error(&e);
        }

        // Sleep for the configured poll interval, checking shutdown signal frequently
        sleep_with_shutdown_check(Duration::from_secs(cfg.poll_interval_seconds), &shutdown);
    }

    log::log_info("Received shutdown signal, exiting cleanly");
    std::process::exit(0);
}

fn sleep_with_shutdown_check(duration: Duration, shutdown: &Arc<AtomicBool>) {
    let check_interval = Duration::from_millis(200);
    let mut remaining = duration;

    loop {
        if shutdown.load(Ordering::SeqCst) {
            break;
        }

        let sleep_time = if remaining > check_interval {
            check_interval
        } else {
            remaining
        };

        thread::sleep(sleep_time);
        remaining = remaining.saturating_sub(sleep_time);

        if remaining.is_zero() {
            break;
        }
    }
}
