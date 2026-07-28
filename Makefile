.PHONY: install uninstall

HOOK_DIR := $(HOME)/.task/hooks

install:
	install -D -m 755 on-modify.01-aw-watcher-taskwarrior.py $(HOOK_DIR)/on-modify.01-aw-watcher-taskwarrior.py
	install -D -m 644 recording_control.py $(HOOK_DIR)/recording_control.py
	@echo "✓ Installed tw-hook-aw-watcher to $(HOOK_DIR)"

uninstall:
	rm -f $(HOOK_DIR)/on-modify.01-aw-watcher-taskwarrior.py
	rm -f $(HOOK_DIR)/recording_control.py
	@echo "✓ Uninstalled tw-hook-aw-watcher from $(HOOK_DIR)"
