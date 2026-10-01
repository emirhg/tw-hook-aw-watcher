use serde::{Deserialize, Serialize};
use std::process::Command;
use std::time::Duration;
use std::thread;
use chrono::{DateTime, Utc};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RawTwTask {
    pub uuid: String,
    pub description: String,
    #[serde(default)]
    pub project: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    pub start: String, // "20260723T200958Z"
}

#[derive(Debug, Clone, PartialEq)]
pub struct ActiveTask {
    pub uuid: Option<String>,
    pub title: String,
    pub project: String,
    pub tags: Vec<String>,
}

impl ActiveTask {
    pub fn default_task() -> Self {
        ActiveTask {
            uuid: None,
            title: String::new(),
            project: "No project assigned".to_string(),
            tags: Vec::new(),
        }
    }
}

#[derive(Debug)]
pub enum TaskQueryError {
    Spawn(std::io::Error),
    Timeout,
    NonZeroExit { code: Option<i32>, stderr: String },
    InvalidJson(serde_json::Error),
}

impl std::fmt::Display for TaskQueryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TaskQueryError::Spawn(e) => write!(f, "failed to spawn task command: {}", e),
            TaskQueryError::Timeout => write!(f, "task command timed out after 5 seconds"),
            TaskQueryError::NonZeroExit { code, stderr } => {
                write!(
                    f,
                    "task command exited with status {}: {}",
                    code.map(|c| c.to_string()).unwrap_or_else(|| "unknown".to_string()),
                    if stderr.is_empty() { "(no stderr)" } else { stderr }
                )
            }
            TaskQueryError::InvalidJson(e) => write!(f, "failed to parse task JSON: {}", e),
        }
    }
}

pub fn query_active_task() -> Result<ActiveTask, TaskQueryError> {
    let output = run_task_export_with_timeout(Duration::from_secs(5))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        return Ok(ActiveTask::default_task());
    }

    let tasks: Vec<RawTwTask> = serde_json::from_str(trimmed)
        .map_err(TaskQueryError::InvalidJson)?;

    if tasks.is_empty() {
        return Ok(ActiveTask::default_task());
    }

    Ok(pick_latest(tasks))
}

fn run_task_export_with_timeout(timeout: Duration) -> Result<std::process::Output, TaskQueryError> {
    let mut child = Command::new("task")
        .args(["rc.hooks:off", "+ACTIVE", "export"])
        .spawn()
        .map_err(TaskQueryError::Spawn)?;

    let start = std::time::Instant::now();
    let check_interval = Duration::from_millis(50);

    loop {
        match child.try_wait().map_err(TaskQueryError::Spawn)? {
            Some(_status) => {
                let output = Command::new("task")
                    .args(["rc.hooks:off", "+ACTIVE", "export"])
                    .output()
                    .map_err(TaskQueryError::Spawn)?;

                if !output.status.success() {
                    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
                    return Err(TaskQueryError::NonZeroExit {
                        code: output.status.code(),
                        stderr,
                    });
                }
                return Ok(output);
            }
            None => {
                if start.elapsed() > timeout {
                    let _ = child.kill();
                    return Err(TaskQueryError::Timeout);
                }
                thread::sleep(check_interval);
            }
        }
    }
}

fn pick_latest(mut tasks: Vec<RawTwTask>) -> ActiveTask {
    tasks.sort_by(|a, b| {
        let ta = parse_tw_timestamp(&a.start);
        let tb = parse_tw_timestamp(&b.start);
        tb.cmp(&ta) // descending: most-recent first
    });
    let chosen = tasks.into_iter().next().unwrap();
    ActiveTask {
        uuid: Some(chosen.uuid),
        title: chosen.description,
        project: chosen.project.unwrap_or_else(|| "No project".to_string()),
        tags: chosen.tags,
    }
}

fn parse_tw_timestamp(s: &str) -> Option<DateTime<Utc>> {
    chrono::NaiveDateTime::parse_from_str(s, "%Y%m%dT%H%M%SZ")
        .ok()
        .map(|naive| DateTime::<Utc>::from_naive_utc_and_offset(naive, Utc))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Datelike;

    #[test]
    fn test_empty_array_yields_default_task() {
        let json = "[]";
        let tasks: Vec<RawTwTask> = serde_json::from_str(json).unwrap();
        let result = if tasks.is_empty() {
            ActiveTask::default_task()
        } else {
            pick_latest(tasks)
        };

        assert_eq!(result.title, "");
        assert_eq!(result.project, "No project assigned");
        assert_eq!(result.tags, vec![] as Vec<String>);
        assert_eq!(result.uuid, None);
    }

    #[test]
    fn test_single_task_parses_correctly() {
        let json = r#"[{
            "uuid": "test-uuid-123",
            "description": "Test task",
            "project": "MyProject",
            "tags": ["tag1", "tag2"],
            "start": "20260723T200958Z"
        }]"#;

        let tasks: Vec<RawTwTask> = serde_json::from_str(json).unwrap();
        let result = pick_latest(tasks);

        assert_eq!(result.uuid, Some("test-uuid-123".to_string()));
        assert_eq!(result.title, "Test task");
        assert_eq!(result.project, "MyProject");
        assert_eq!(result.tags, vec!["tag1", "tag2"]);
    }

    #[test]
    fn test_missing_project_defaults_to_no_project() {
        let json = r#"[{
            "uuid": "test-uuid",
            "description": "Task without project",
            "tags": [],
            "start": "20260723T200958Z"
        }]"#;

        let tasks: Vec<RawTwTask> = serde_json::from_str(json).unwrap();
        let result = pick_latest(tasks);

        assert_eq!(result.project, "No project");
    }

    #[test]
    fn test_multiple_active_tasks_picks_most_recent_start() {
        let json = r#"[
            {
                "uuid": "uuid-1",
                "description": "Older task",
                "tags": [],
                "start": "20260723T100000Z"
            },
            {
                "uuid": "uuid-2",
                "description": "Newer task",
                "tags": [],
                "start": "20260723T200000Z"
            }
        ]"#;

        let tasks: Vec<RawTwTask> = serde_json::from_str(json).unwrap();
        let result = pick_latest(tasks);

        assert_eq!(result.uuid, Some("uuid-2".to_string()));
        assert_eq!(result.title, "Newer task");
    }

    #[test]
    fn test_unparseable_start_sorts_last() {
        let json = r#"[
            {
                "uuid": "uuid-1",
                "description": "Valid start",
                "tags": [],
                "start": "20260723T200000Z"
            },
            {
                "uuid": "uuid-2",
                "description": "Invalid start",
                "tags": [],
                "start": "not-a-timestamp"
            }
        ]"#;

        let tasks: Vec<RawTwTask> = serde_json::from_str(json).unwrap();
        let result = pick_latest(tasks);

        // Valid timestamp should be picked over invalid one
        assert_eq!(result.uuid, Some("uuid-1".to_string()));
    }

    #[test]
    fn test_parse_tw_timestamp_format() {
        let ts = "20260723T200958Z";
        let result = parse_tw_timestamp(ts);
        assert!(result.is_some());

        let dt = result.unwrap();
        assert_eq!(dt.year(), 2026);
        assert_eq!(dt.month(), 7);
        assert_eq!(dt.day(), 23);
    }

    #[test]
    fn test_parse_tw_timestamp_invalid() {
        let ts = "garbage";
        let result = parse_tw_timestamp(ts);
        assert!(result.is_none());
    }
}
