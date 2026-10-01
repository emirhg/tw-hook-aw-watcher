pub fn log_info(msg: &str) {
    println!("[{}] INFO  {}", chrono::Utc::now().to_rfc3339(), msg);
}

pub fn log_error(msg: &str) {
    eprintln!("[{}] ERROR {}", chrono::Utc::now().to_rfc3339(), msg);
}
